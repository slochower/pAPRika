import logging
import os

import numpy as np
from parmed.structure import Structure as ParmedStructureClass

from paprika.build.dummy import extract_dummy_atoms
from paprika.restraints.utils import get_bias_potential_type, parse_window
from paprika.utils import get_key, return_parmed_structure

logger = logging.getLogger(__name__)

_PI_ = np.pi


class Plumed:
    """
    This class converts restraints generated in `pAPRika` :class:`paprika.restraints.DAT_restraint` into `Plumed
    <https://www.plumed.org/>`_ restraints.

    .. note ::
            The ``Plumed`` module is described in the reference below and the source code is available on Github
            https://github.com/plumed/plumed2

            `The PLUMED consortium. Promoting transparency and reproducibility in enhanced molecular simulations,
            Nat. Methods 16, 670 (2019)`

    .. todo::
        possibly change this module to use the python wrapper of Plumed.

    Examples
    --------
        >>> plumed = Plumed()
        >>> plumed.file_name = 'plumed.dat'
        >>> plumed.path = './windows'
        >>> plumed.window_list = window_list
        >>> plumed.restraint_list = restraint_list
        >>> plumed.dump_to_file()

    The commands above will write the restraints to ``windows/*/plumed.dat`` and contains the Plumed-style restraints

    .. code-block::

        UNITS LENGTH=A ENERGY=kcal/mol TIME=ns
        # Collective variables
        c1 : DISTANCE ATOMS=175,150 NOPBC
        c2 : ANGLE    ATOMS=176,175,150 NOPBC
        c3 : ANGLE    ATOMS=175,150,165 NOPBC
        # Bias potential
        RESTRAINT   ARG=c7  AT=  6.000 KAPPA=  10.00
        RESTRAINT   ARG=c8  AT=  3.142 KAPPA= 200.00
        RESTRAINT   ARG=c9  AT=  3.142 KAPPA= 200.00

    The positional restraints on dummy atoms, however, is not added automatically. This restraints on the dummy
    atoms can be added to ``windows/*/plumed.dat`` using the code below.

        >>> for window in window_list:
        >>>     structure = pmd.load_file("topology.prmtop", "coordinates.rst7")
        >>>     plumed.add_dummy_atoms_to_file(structure, window)

    This appends the file with the following

    .. code-block::

        # Dummy Atoms
        dm1: POSITION ATOM=123 NOPBC
        dm2: POSITION ATOM=124 NOPBC
        dm3: POSITION ATOM=125 NOPBC
        RESTRAINT ...
        ARG=dm1.x,dm1.y,dm1.z,dm2.x,dm2.y,dm2.z,dm3.x,dm3.y,dm3.z,
        AT=18.600,19.020,27.950,18.600,19.020,24.950,18.600,21.220,22.750,
        KAPPA=100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,
        LABEL=dummy
        ... RESTRAINT
    """

    @property
    def path(self):
        """
        os.PathLike: The parent directory that contains the APR simulation windows.
        """
        return self._path

    @path.setter
    def path(self, value: str):
        self._path = value

    @property
    def file_name(self):
        """
        str: The Plumed file name where the restraints will be written to.
        """
        return self._file_name

    @file_name.setter
    def file_name(self, value: str):
        self._file_name = value

    @property
    def window_list(self):
        """
        list: The list of APR windows where the Plumed files will be stored.
        """
        return self._window_list

    @window_list.setter
    def window_list(self, value: list):
        self._window_list = value

    @property
    def restraint_list(self):
        """
        list: The list of restraints to convert.
        """
        return self._restraint_list

    @restraint_list.setter
    def restraint_list(self, value: list):
        self._restraint_list = value

    @property
    def uses_legacy_k(self):
        """
        bool: Option to specify whether the force constant parsed into ``DAT_restraint`` uses
        legacy force constant.

        .. note ::
            `AMBER`-style force constants have their value multiplied by a factor of 1/2 whereas
            `GROMACS`/`NAMD`-style do not. Plumed follows the `GROMACS`/`NAMD`-style convention
            for the force constant and the equations below demonstrates this point.

            .. math::
               :nowrap:

               $$
               \\begin{eqnarray}
               U_{Amber} & = & K (x-x_{0})^2 \\\\
               U_{Plumed} & = & \\frac{1}{2} k (x-x_{0})^2 \\\\
               \\therefore K_{Amber} & = & \\frac{1}{2} k_{Plumed}
               \\end{eqnarray}
               $$

            i.e. ``uses_legacy_k`` is set to True (default) the force constants will be multiplied by 2.
        """
        return self._uses_legacy_k

    @uses_legacy_k.setter
    def uses_legacy_k(self, value: bool):
        self._uses_legacy_k = value

    @property
    def units(self):
        """
        dict: Dictionary of units for Plumed as strings. The dictionary requires the key values
        of ``energy``, ``length``, ``time``.
        """
        return self._units

    @units.setter
    def units(self, value: dict):
        self._units = value

    def __init__(self):
        self._file_name = "plumed.dat"
        self._restraint_list = None
        self._window_list = None
        self._path = "./"
        self._uses_legacy_k = True
        self.k_factor = 1.0
        self._units = None
        self.header_line = None
        self.group_index = None
        self.group_atoms = None

    def _initialize(self):
        # Set factor for spring constant
        if self.uses_legacy_k:
            self.k_factor = 2.0

        # Check units
        # NOTE: We have to resort to strings until (if) we migrate all quantities to Pint/SimTK units
        if self.units is None:
            self.units = {"energy": "kcal/mol", "length": "A", "time": "ns"}
        _check_plumed_units(self.units)

        # header line
        self.header_line = (
            f"UNITS LENGTH={self.units['length']} "
            f"ENERGY={self.units['energy']} "
            f"TIME={self.units['time']}"
        )

    def dump_to_file(self):
        """
        Write the `Plumed`-style restraints to file.
        """

        self._initialize()

        # Loop over APR windows
        for windows in self.window_list:

            window, phase = parse_window(windows)

            # Check if file exist and write header line
            with open(os.path.join(self.path, windows, self.file_name), "w") as file:
                file.write(self.header_line + "\n")

            cv_index = 1
            cv_dict = {}
            cv_lines = []
            bias_lines = []

            self.group_index = 1
            self.group_atoms = {}

            # Parse each restraint in the list
            for restraint in self.restraint_list:
                # Skip restraint if the target or force constant is not defined.
                # Example: wall restraints only used during the attach phase.
                try:
                    target = restraint.phase[phase]["targets"][window]
                    force_constant = (
                        restraint.phase[phase]["force_constants"][window]
                        * self.k_factor
                    )
                except TypeError:
                    continue

                # Convert list to comma-separated string
                atom_index = self._get_atom_indices(restraint)
                atom_string = ",".join(map(str, atom_index))

                # Determine collective variable type
                colvar_type = "distance"
                if len(atom_index) == 3:
                    colvar_type = "angle"
                    target *= _PI_ / 180.0
                elif len(atom_index) == 4:
                    colvar_type = "torsion"
                    target *= _PI_ / 180.0

                # Determine bias type for this restraint
                bias_type = get_bias_potential_type(restraint, phase, window)

                # Append cv strings to lists
                # The code below prevents duplicate cv definition.
                # While not necessary, it makes the plumed file cleaner.
                if not get_key(cv_dict, atom_string):
                    cv_key = f"c{cv_index}"
                    cv_dict[cv_key] = atom_string

                    cv_lines.append(
                        f"{cv_key}: {colvar_type.upper()} ATOMS={atom_string} NOPBC\n"
                    )
                    bias_lines.append(
                        f"{bias_type.upper()} ARG={cv_key} AT={target:.4f} KAPPA={force_constant:.2f}\n"
                    )
                else:
                    cv_key = get_key(cv_dict, atom_string)[0]

                    bias_lines.append(
                        f"{bias_type.upper()} ARG={cv_key} AT={target:.4f} KAPPA={force_constant:.2f}\n"
                    )

                # Increment cv index
                cv_index += 1

            # Write collective variables to file
            self._write_colvar_to_file(windows, cv_lines, bias_lines)

    def _write_colvar_to_file(self, window, cv_list, bias_list):
        with open(os.path.join(self.path, window, self.file_name), "a") as file:
            if len(self.group_atoms) != 0:
                file.write("# Centroid groups\n")
                for key, value in self.group_atoms.items():
                    file.write(f"{key}: COM ATOMS={value}\n")

            file.write("# Collective variables\n")
            for line in cv_list:
                file.write(line)

            file.write("# Bias potentials\n")
            for line in bias_list:
                file.write(line)

    def _get_atom_indices(self, restraint):
        # Check atom index setting
        index_shift = 0
        if not restraint.amber_index:
            index_shift = 1
            logger.debug("Atom indices starts from 0 --> shifting indices by 1.")

        # Collect DAT atom indices
        atom_index = []

        if not restraint.group1:
            atom_index.append(restraint.index1[0] + index_shift)
        else:
            igr1 = ""
            for index in restraint.index1:
                igr1 += "{},".format(index + index_shift)

            if not get_key(self.group_atoms, igr1):
                self.group_atoms[f"g{self.group_index}"] = igr1
                self.group_index += 1

            atom_index.append(get_key(self.group_atoms, igr1)[0])

        if not restraint.group2:
            atom_index.append(restraint.index2[0] + index_shift)
        else:
            igr2 = ""
            for index in restraint.index2:
                igr2 += "{},".format(index + index_shift)

            if not get_key(self.group_atoms, igr2):
                self.group_atoms[f"g{self.group_index}"] = igr2
                self.group_index += 1

            atom_index.append(get_key(self.group_atoms, igr2)[0])

        if restraint.index3 and not restraint.group3:
            atom_index.append(restraint.index3[0] + index_shift)
        elif restraint.group3:
            igr3 = ""
            for index in restraint.index3:
                igr3 += "{},".format(index + index_shift)

            if not get_key(self.group_atoms, igr3):
                self.group_atoms[f"g{self.group_index}"] = igr3
                self.group_index += 1

            atom_index.append(get_key(self.group_atoms, igr3)[0])

        if restraint.index4 and not restraint.group4:
            atom_index.append(restraint.index4[0] + index_shift)
        elif restraint.group4:
            igr4 = ""
            for index in restraint.index4:
                igr4 += "{},".format(index + index_shift)

            if not get_key(self.group_atoms, igr4):
                self.group_atoms[f"g{self.group_index}"] = igr4
                self.group_index += 1

            atom_index.append(get_key(self.group_atoms, igr4)[0])

        return atom_index

    def add_dummy_atom_restraints(self, structure, window, path=None):
        """
        Add positional restraints on dummy atoms to the restraint files.

        Parameters
        ----------
        structure: os.PathLike or :class:`parmed.Structure`
            The reference structure that is used to determine the absolute coordinate of the dummy atoms.
        window: str
            APR window where the structure is stored for extracting the dummy atom positions.
        path: os.PathLike, optional, default=None
            Path of the restraint file. If set to ``None`` (default) self.path will be used.

        """
        # Load structure file
        if isinstance(structure, str):
            structure = return_parmed_structure(structure)
        elif isinstance(structure, ParmedStructureClass):
            pass
        else:
            raise Exception(
                "add_dummy_atoms_to_file does not support the type associated with structure: "
                + type(structure)
            )

        # Extract dummy atoms
        dummy_atoms = extract_dummy_atoms(structure, serial=True)

        # Write dummy atom info to plumed file
        if path is not None:
            restraint_file = os.path.join(path, window, self.file_name)
        else:
            restraint_file = os.path.join(self.path, window, self.file_name)

        if os.path.isfile(restraint_file):
            with open(restraint_file, "a") as file:
                self._write_dummy_to_file(file, dummy_atoms)
        else:
            raise Exception(f"ERROR: '{restraint_file}' file does not exists!")

    @staticmethod
    def _write_dummy_to_file(file, dummy_atoms, kpos=100.0):
        """
        Append to the "plumed.dat" file the dummy atoms colvar definition and position restraints

        Parameters
        ----------
        file : class '_io.TextIOWrapper'
            The file object handle to save the plumed file.
        dummy_atoms : dict
            Dictionary containing information about the dummy atoms.
        kpos : float
            Spring constant used to restrain dummy atoms (kcal/mol/A^2).

        Examples
        --------
        .. code-block::

            dm1: POSITION ATOM = 170 NOPBC
            dm2: POSITION ATOM = 171 NOPBC
            dm3: POSITION ATOM = 172 NOPBC
            RESTRAINT...
            ARG = dm1.x, dm1.y, dm1.z, dm2.x, dm2.y, dm2.z, dm3.x, dm3.y, dm3.z
            AT = 19.68, 20.3, 26.9, 19.68, 20.3, 23.9, 19.68, 22.5, 21.7
            KAPPA = 50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0
            ...
            RESTRAINT

        """

        file.write("# Dummy Atoms\n")
        file.write(f"dm1: POSITION ATOM={dummy_atoms['DM1']['idx']} NOPBC\n")
        file.write(f"dm2: POSITION ATOM={dummy_atoms['DM2']['idx']} NOPBC\n")
        file.write(f"dm3: POSITION ATOM={dummy_atoms['DM3']['idx']} NOPBC\n")

        arg = "dm1.x,dm1.y,dm1.z," "dm2.x,dm2.y,dm2.z," "dm3.x,dm3.y,dm3.z,"

        at = (
            f"{dummy_atoms['DM1']['pos'][0]:0.3f},"
            f"{dummy_atoms['DM1']['pos'][1]:0.3f},"
            f"{dummy_atoms['DM1']['pos'][2]:0.3f},"
        )
        at += (
            f"{dummy_atoms['DM2']['pos'][0]:0.3f},"
            f"{dummy_atoms['DM2']['pos'][1]:0.3f},"
            f"{dummy_atoms['DM2']['pos'][2]:0.3f},"
        )
        at += (
            f"{dummy_atoms['DM3']['pos'][0]:0.3f},"
            f"{dummy_atoms['DM3']['pos'][1]:0.3f},"
            f"{dummy_atoms['DM3']['pos'][2]:0.3f},"
        )

        kappa = (
            f"{kpos:0.1f},{kpos:0.1f},{kpos:0.1f},"
            f"{kpos:0.1f},{kpos:0.1f},{kpos:0.1f},"
            f"{kpos:0.1f},{kpos:0.1f},{kpos:0.1f},"
        )

        file.write("RESTRAINT ...\n")
        file.write(f"ARG={arg}\n")
        file.write(f"AT={at}\n")
        file.write(f"KAPPA={kappa}\n")
        file.write("LABEL=dummy\n")
        file.write("... RESTRAINT\n")


def _check_plumed_units(units):
    """
    Checks the specified units and makes sure that its supported.
    """
    if units["energy"] not in ["kj/mol", "kcal/mol"]:
        raise Exception(
            f"Specified unit for energy ({units['energy']}) is not supported."
        )

    if units["length"] not in ["nm", "A"]:
        raise Exception(
            f"Specified unit for length ({units['length']}) is not supported."
        )

    if units["time"] not in ["ps", "fs", "ns"]:
        raise Exception(f"Specified unit for time ({units['time']}) is not supported.")

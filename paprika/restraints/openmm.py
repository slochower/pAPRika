"""A module aimed at applying restraints directly to OpenMM systems."""
import logging

import numpy as np
import parmed as pmd

logger = logging.getLogger(__name__)
_PI_ = np.pi


def apply_positional_restraints(coordinate_path: str, system, force_group: int = 15):
    """A utility function which will add OpenMM harmonic positional restraints to
    any dummy atoms found within a system to restrain them to their initial
    positions.

    Parameters
    ----------
    coordinate_path : str
        The path to the coordinate file which the restraints will be applied to.
        This should contain either the host or the complex, the dummy atoms and
        and solvent.
    system : :class:`openmm.System`
        The system object to add the positional restraints to.
    force_group : int, optional
        The force group to add the positional restraints to.
    """

    from simtk import openmm, unit

    # noinspection PyTypeChecker
    structure: pmd.Structure = pmd.load_file(coordinate_path, structure=True)

    for atom in structure.atoms:

        if atom.name == "DUM":
            positional_restraint = openmm.CustomExternalForce(
                "k * ((x-x0)^2 + (y-y0)^2 + (z-z0)^2)"
            )
            positional_restraint.addPerParticleParameter("k")
            positional_restraint.addPerParticleParameter("x0")
            positional_restraint.addPerParticleParameter("y0")
            positional_restraint.addPerParticleParameter("z0")

            # I haven't found a way to get this to use ParmEd's unit library here.
            # ParmEd correctly reports `atom.positions` as units of Ångstroms.
            # But then we can't access atom indices. Using `atom.xx` works for
            # coordinates, but is unitless.
            k = 50.0 * unit.kilocalories_per_mole / unit.angstroms ** 2

            x0 = 0.1 * atom.xx * unit.nanometers
            y0 = 0.1 * atom.xy * unit.nanometers
            z0 = 0.1 * atom.xz * unit.nanometers

            positional_restraint.addParticle(atom.idx, [k, x0, y0, z0])
            system.addForce(positional_restraint)
            positional_restraint.setForceGroup(force_group)


def apply_dat_restraint(
    system, restraint, phase, window_number, flat_bottom=False, force_group=None
):
    """A utility function which takes in pAPRika restraints and applies the
    restraints to an OpenMM System object.

    Parameters
    ----------
    system : :class:`openmm.System`
        The system object to add the positional restraints to.
    restraint : list
        List of pAPRika defined restraints
    phase : str
        Phase of calculation ("attach", "pull" or "release")
    window_number : int
        The corresponding window number of the current phase
    flat_bottom : bool, optional
        Specify whether the restraint is a flat bottom potential
    force_group : int, optional
        The force group to add the positional restraints to.

    """

    from simtk import openmm, unit

    assert phase in {"attach", "pull", "release"}

    if flat_bottom and phase == "attach" and restraint.mask3:
        flat_bottom_force = openmm.CustomAngleForce(
            "step(-(theta - theta_0)) * k * (theta - theta_0)^2"
        )
        # If theta is greater than theta_0, then the argument to step is negative,
        # which means the force is off.
        flat_bottom_force.addPerAngleParameter("k")
        flat_bottom_force.addPerAngleParameter("theta_0")

        theta_0 = 91.0 * unit.degrees
        k = (
            restraint.phase[phase]["force_constants"][window_number]
            * unit.kilocalories_per_mole
            / unit.radian ** 2
        )
        flat_bottom_force.addAngle(
            restraint.index1[0],
            restraint.index2[0],
            restraint.index3[0],
            [k, theta_0],
        )
        system.addForce(flat_bottom_force)
        if force_group:
            flat_bottom_force.setForceGroup(force_group)

        return

    elif flat_bottom and phase == "attach" and not restraint.mask3:
        flat_bottom_force = openmm.CustomBondForce("step((r - r_0)) * k * (r - r_0)^2")
        # If x is greater than x_0, then the argument to step is positive, which means
        # the force is on.
        flat_bottom_force.addPerBondParameter("k")
        flat_bottom_force.addPerBondParameter("r_0")

        r_0 = restraint.phase[phase]["targets"][window_number] * unit.angstrom
        k = (
            restraint.phase[phase]["force_constants"][window_number]
            * unit.kilocalories_per_mole
            / unit.radian ** 2
        )
        flat_bottom_force.addBond(
            restraint.index1[0],
            restraint.index2[0],
            [k, r_0],
        )
        system.addForce(flat_bottom_force)
        if force_group:
            flat_bottom_force.setForceGroup(force_group)

        return

    elif flat_bottom and phase == "pull":
        return
    elif flat_bottom and phase == "release":
        return

    if restraint.mask2 and not restraint.mask3:
        if not restraint.group1 and not restraint.group2:
            bond_restraint = openmm.CustomBondForce("k * (r - r_0)^2")
            bond_restraint.addPerBondParameter("k")
            bond_restraint.addPerBondParameter("r_0")

            r_0 = restraint.phase[phase]["targets"][window_number] * unit.angstroms
            k = (
                restraint.phase[phase]["force_constants"][window_number]
                * unit.kilocalories_per_mole
                / unit.angstrom ** 2
            )
            bond_restraint.addBond(restraint.index1[0], restraint.index2[0], [k, r_0])
            system.addForce(bond_restraint)
        else:
            bond_restraint = openmm.CustomCentroidBondForce(
                2, "k * (distance(g1, g2) - r_0)^2"
            )
            bond_restraint.addPerBondParameter("k")
            bond_restraint.addPerBondParameter("r_0")
            r_0 = restraint.phase[phase]["targets"][window_number] * unit.angstroms
            k = (
                restraint.phase[phase]["force_constants"][window_number]
                * unit.kilocalories_per_mole
                / unit.angstrom ** 2
            )
            g1 = bond_restraint.addGroup(restraint.index1)
            g2 = bond_restraint.addGroup(restraint.index2)
            bond_restraint.addBond([g1, g2], [k, r_0])
            system.addForce(bond_restraint)

        if force_group:
            bond_restraint.setForceGroup(force_group)

    elif restraint.mask3 and not restraint.mask4:
        if not restraint.group1 and not restraint.group2 and not restraint.group3:
            angle_restraint = openmm.CustomAngleForce("k * (theta - theta_0)^2")
            angle_restraint.addPerAngleParameter("k")
            angle_restraint.addPerAngleParameter("theta_0")

            theta_0 = restraint.phase[phase]["targets"][window_number] * unit.degrees
            k = (
                restraint.phase[phase]["force_constants"][window_number]
                * unit.kilocalories_per_mole
                / unit.radian ** 2
            )
            angle_restraint.addAngle(
                restraint.index1[0],
                restraint.index2[0],
                restraint.index3[0],
                [k, theta_0],
            )
            system.addForce(angle_restraint)
        else:
            # Probably needs openmm.CustomCentroidAngleForce (?)
            raise NotImplementedError
        if force_group:
            angle_restraint.setForceGroup(force_group)

    elif restraint.mask4:
        if (
            not restraint.group1
            and not restraint.group2
            and not restraint.group3
            and not restraint.group4
        ):
            dihedral_restraint = openmm.CustomTorsionForce(
                f"k * min(min(abs(theta - theta_0), abs(theta - theta_0 + 2 * "
                f"{_PI_})), abs(theta - theta_0 - 2 * {_PI_}))^2"
            )
            dihedral_restraint.addPerTorsionParameter("k")
            dihedral_restraint.addPerTorsionParameter("theta_0")

            theta_0 = restraint.phase[phase]["targets"][window_number] * unit.degrees
            k = (
                restraint.phase[phase]["force_constants"][window_number]
                * unit.kilocalories_per_mole
                / unit.radian ** 2
            )
            dihedral_restraint.addTorsion(
                restraint.index1[0],
                restraint.index2[0],
                restraint.index3[0],
                restraint.index4[0],
                [k, theta_0],
            )
            system.addForce(dihedral_restraint)
        else:
            # Probably needs openmm.CustomCentroidTorsionForce (?)
            raise NotImplementedError
        if force_group:
            dihedral_restraint.setForceGroup(force_group)

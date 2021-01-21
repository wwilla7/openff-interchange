from pathlib import Path
from typing import IO, Dict

import numpy as np
import parmed as pmd

from openff.system import unit
from openff.system.components.system import System

lookup_dict = dict((v, k) for k, v in pmd.periodic_table.AtomicNum.items())


def to_gro(openff_sys: System, file_path: Path):
    """
    Write a .gro file. See
    https://manual.gromacs.org/documentation/current/reference-manual/file-formats.html#gro
    for more details, including the recommended C-style one-liners

    This code is partially copied from InterMol, see
    https://github.com/shirtsgroup/InterMol/tree/v0.1/intermol/gromacs

    """
    with open(file_path, "w") as gro:
        gro.write("Generated by OpenFF System\n")
        gro.write(f"{len(openff_sys.positions)}\n")
        for idx, atom in enumerate(openff_sys.topology.topology_atoms):  # type: ignore
            atom_name = lookup_dict[atom.atomic_number] + str(idx + 1)
            # TODO: Make sure these are in nanometers
            pos = openff_sys.positions[idx].to(unit.nanometer).magnitude
            gro.write(
                # If writing velocities:
                # "\n%5d%-5s%5s%5d%8.3f%8.3f%8.3f%8.4f%8.4f%8.4f" % (
                "%5d%-5s%5s%5d%8.3f%8.3f%8.3f\n"
                % (
                    1 % 100000,  # residue index
                    "FOO",  # residue name
                    atom_name,
                    (idx + 1) % 100000,
                    pos[0],
                    pos[1],
                    pos[2],
                )
            )

        # TODO: Ensure nanometers
        box = openff_sys.box.to(unit.nanometer).magnitude
        # Check for rectangular
        if (box == np.diag(np.diagonal(box))).all():
            for i in range(3):
                gro.write("{0:11.7f}".format(box[i, i]))
        else:
            for i in range(3):
                gro.write("{0:11.7f}".format(box[i, i]))
            for i in range(3):
                for j in range(3):
                    if i != j:
                        gro.write("{0:11.7f}".format(box[i, j]))

        gro.write("\n")


def to_top(openff_sys: System, file_path: Path):
    """
    Write a .gro file. See
    https://manual.gromacs.org/documentation/current/reference-manual/file-formats.html#top
    for more details.

    This code is partially copied from InterMol, see
    https://github.com/shirtsgroup/InterMol/tree/v0.1/intermol/gromacs

    """
    with open(file_path, "w") as top_file:
        top_file.write("; Generated by OpenFF System\n")
        _write_top_defaults(openff_sys, top_file)
        typemap = _write_atomtypes(openff_sys, top_file)
        # TODO: Write [ nonbond_params ] section
        _write_moleculetypes(openff_sys, top_file)
        _write_atoms(openff_sys, top_file, typemap)
        _write_valence(openff_sys, top_file)
        _write_system(openff_sys, top_file)


def _write_top_defaults(openff_sys: System, top_file: IO):
    """Write [ defaults ] section"""
    top_file.write("[ defaults ]\n")
    top_file.write("; nbfunc\tcomb-rule\tgen-pairs\tfudgeLJ\tfudgeQQ\n")
    top_file.write(
        "{0:6d}\t{1:6s}\t{2:6s} {3:8.6f} {4:8.6f}\n\n".format(
            # self.system.nonbonded_function,
            # self.lookup_gromacs_combination_rules[self.system.combination_rule],
            # self.system.genpairs,
            # self.system.lj_correction,
            # self.system.coulomb_correction,
            1,
            str(2),
            "yes",
            openff_sys.handlers["vdW"].scale_14,  # type: ignore
            openff_sys.handlers["Electrostatics"].scale_14,  # type: ignore
        )
    )


def _write_atomtypes(openff_sys: System, top_file: IO) -> Dict:
    """Write [ atomtypes ] section"""
    typemap = dict()
    elements: Dict[str, int] = dict()

    for atom_idx, atom in enumerate(openff_sys.topology.topology_atoms):  # type: ignore
        atomic_number = atom.atomic_number
        element = lookup_dict[atomic_number]
        # TODO: Use this key to condense, see parmed.openmm._process_nobonded
        # parameters = _get_lj_parameters([*parameters.values()])
        # key = tuple([*parameters.values()])

        if element not in elements.keys():
            elements[element] = 0

        atom_type = f"{element}{elements[element]}"
        typemap[atom_idx] = atom_type

    top_file.write("[ atomtypes ]\n")
    top_file.write(
        ";type, bondingtype, atomic_number, mass, charge, ptype, sigma, epsilon\n"
    )

    for atom_idx, atom_type in typemap.items():
        atom = openff_sys.topology.atom(atom_idx)  # type: ignore
        element = lookup_dict[atom.atomic_number]
        parameters = _get_lj_parameters(openff_sys, atom_idx)
        sigma = parameters["sigma"].to(unit.nanometer).magnitude  # type: ignore
        epsilon = parameters["epsilon"].to(unit.Unit("kilojoule / mole")).magnitude  # type: ignore
        top_file.write(
            "{0:<11s} {1:5s} {2:6d} {3:18.8f} {4:18.8f} {5:5s} {6:18.8e} {7:18.8e}".format(
                atom_type,  # atom type
                "XX",  # atom "bonding type", i.e. bond class
                atom.atomic_number,
                pmd.periodic_table.Mass[element],
                0.0,  # charge, overriden later in [ atoms ]
                "A",  # ptype
                sigma,
                epsilon,
            )
        )
        top_file.write("\n")

    return typemap


def _write_moleculetypes(openff_sys: System, top_file: IO):
    if openff_sys.topology.n_topology_molecules > 1:  # type: ignore
        raise Exception
    """Write the [ moleculetype ] section"""
    top_file.write("[ moleculetype ]\n")
    top_file.write("; Name\tnrexcl\n")
    top_file.write("FOO\t3\n\n")


def _write_atoms(openff_sys: System, top_file: IO, typemap: Dict):
    """Write the [ atoms ] section"""
    top_file.write("[ atoms ]\n")
    top_file.write(";num, type, resnum, resname, atomname, cgnr, q, m\n")
    for atom_idx, atom in enumerate(openff_sys.topology.topology_atoms):  # type: ignore
        atom_type = typemap[atom_idx]
        element = lookup_dict[atom.atomic_number]
        mass = pmd.periodic_table.Mass[element]
        charge = (
            openff_sys.handlers["Electrostatics"].charge_map[str((atom_idx,))].magnitude  # type: ignore
        )
        top_file.write(
            "{0:6d} {1:18s} {2:6d} {3:8s} {4:8s} {5:6d} "
            "{6:18.8f} {7:18.8f}\n".format(
                atom_idx + 1,
                atom_type,
                1,  # residue_name,
                "FOO",  # residue_index,
                element,
                atom_idx + 1,  # cgnr
                charge,
                mass,
            )
        )


def _write_valence(openff_sys: System, top_file: IO):
    """Write the [ bonds ], [ angles ], and [ dihedrals ] sections"""
    _write_bonds(openff_sys, top_file)
    _write_angles(openff_sys, top_file)
    _write_dihedrals(openff_sys, top_file)


def _write_bonds(openff_sys: System, top_file: IO):
    if "Bonds" not in openff_sys.handlers.keys():
        return

    top_file.write("[ bonds ]\n")
    top_file.write("; ai\taj\tfunc\tr\tk\n")

    bond_handler = openff_sys.handlers["Bonds"]
    for bond, key in bond_handler.slot_map.items():
        indices = eval(bond)
        params = bond_handler.potentials[key].parameters
        k = params["k"].to(unit.Unit("kilojoule / mole / nanometer ** 2")).magnitude
        length = params["length"].to(unit.nanometer).magnitude

        top_file.write(
            "{0:7d} {1:7d} {2:4s} {3:18.8e} {4:18.8e}\n".format(
                indices[0] + 1,  # atom i
                indices[1] + 1,  # atom j
                str(1),  # bond type (functional form)
                length,
                k,
            )
        )

    top_file.write("\n\n")


def _write_angles(openff_sys: System, top_file: IO):
    if "Angles" not in openff_sys.handlers.keys():
        return

    top_file.write("[ angles ]\n")
    top_file.write("; ai\taj\tak\tfunc\tr\tk\n")

    angle_handler = openff_sys.handlers["Angles"]
    for angle, key in angle_handler.slot_map.items():
        indices = eval(angle)
        params = angle_handler.potentials[key].parameters
        k = params["k"].to(unit.Unit("kilojoule / mole / radian ** 2")).magnitude
        theta = params["angle"].to(unit.degree).magnitude

        top_file.write(
            "{0:7d} {1:7d} {2:7d} {3:4s} {4:18.8e} {5:18.8e}\n".format(
                indices[0] + 1,  # atom i
                indices[1] + 1,  # atom j
                indices[2] + 1,  # atom k
                str(1),  # angle type (functional form)
                theta,
                k,
            )
        )

    top_file.write("\n\n")


def _write_dihedrals(openff_sys: System, top_file: IO):
    if "ProperTorsions" not in openff_sys.handlers.keys():
        if "ImproperTorsions" not in openff_sys.handlers.keys():
            return

    top_file.write("[ dihedrals ]\n")
    top_file.write(";    i      j      k      l   func\n")

    proper_torsion_handler = openff_sys.handlers["ProperTorsions"]
    improper_torsion_handler = openff_sys.handlers["ImproperTorsions"]

    for torsion_key, key in proper_torsion_handler.slot_map.items():
        torsion, idx = torsion_key.split("_")
        indices = eval(torsion)
        params = proper_torsion_handler.potentials[key].parameters

        k = params["k"].to(unit.Unit("kilojoule / mol")).magnitude
        periodicity = int(params["periodicity"])
        phase = params["phase"].to(unit.degree).magnitude
        idivf = int(params["idivf"])
        top_file.write(
            "{0:7d} {1:7d} {2:7d} {3:7d} {4:6d} {5:18.8e} {6:18.8e} {7:18.8e}\n".format(
                indices[0] + 1,
                indices[1] + 1,
                indices[2] + 1,
                indices[3] + 1,
                1,
                phase,
                k / idivf,
                periodicity,
            )
        )

    for torsion_key, key in improper_torsion_handler.slot_map.items():
        torsion, idx = torsion_key.split("_")
        indices = eval(torsion)
        params = proper_torsion_handler.potentials[key].parameters

        k = params["k"].to(unit.Unit("kilojoule / mol")).magnitude
        periodicity = int(params["periodicity"])
        phase = params["phase"].to(unit.degree).magnitude
        idivf = int(params["idivf"])
        top_file.write(
            "{0:7d} {1:7d} {2:7d} {3:7d} {4:6d} {5:18.8e} {6:18.8e} {7:18.8e}\n".format(
                indices[0] + 1,
                indices[1] + 1,
                indices[2] + 1,
                indices[3] + 1,
                4,
                phase,
                k / idivf,
                periodicity,
            )
        )


def _write_system(openff_sys: System, top_file: IO):
    """Write the [ system ] section"""
    top_file.write("[ system ]\n")
    top_file.write("; name \n")
    top_file.write("System name\n\n")

    top_file.write("[ molecules ]")
    top_file.write("; Compound\tnmols\n")
    top_file.write("FOO\t1\n")


def _get_lj_parameters(openff_sys: System, atom_idx: int) -> Dict:
    vdw_hander = openff_sys.handlers["vdW"]
    identifier = vdw_hander.slot_map[str((atom_idx,))]
    potential = vdw_hander.potentials[identifier]
    parameters = potential.parameters

    return parameters

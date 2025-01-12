from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from openff.units import unit
from openff.units.elements import MASSES, SYMBOLS

from openff.interchange.components.interchange import Interchange
from openff.interchange.components.toolkit import _get_14_pairs
from openff.interchange.exceptions import (
    MissingAngleError,
    MissingBondError,
    UnsupportedExportError,
)
from openff.interchange.interop.gromacs.models.models import (
    GROMACSAngle,
    GROMACSAtom,
    GROMACSBond,
    GROMACSMolecule,
    GROMACSPair,
    GROMACSSystem,
    LennardJonesAtomType,
    PeriodicImproperDihedral,
    PeriodicProperDihedral,
    RyckaertBellemansDihedral,
)
from openff.interchange.models import TopologyKey

if TYPE_CHECKING:
    from openff.toolkit.topology.molecule import Atom, Molecule

    from openff.interchange.components.potentials import Collection


def _convert(interchange: Interchange) -> GROMACSSystem:
    """Convert an `Interchange` object to `GROMACSSystem`."""
    if "vdW" in interchange.collections:
        nonbonded_function = 1
        scale_lj = interchange["vdW"].scale_14
        _combination_rule = interchange["vdW"].mixing_rule.lower()
        gen_pairs = "yes"
    else:
        raise UnsupportedExportError(
            "Could not find a handler for short-ranged vdW interactions that is compatible "
            "with GROMACS.",
        )

    if _combination_rule == "lorentz-berthelot":
        combination_rule = 2
    elif _combination_rule == "geometric":
        combination_rule = 3
    else:
        raise UnsupportedExportError(
            f"Could not find a GROMACS-compatible combination rule for mixing rule "
            f"{_combination_rule}.",
        )

    scale_electrostatics = interchange["Electrostatics"].scale_14

    system = GROMACSSystem(
        name="FOO",
        nonbonded_function=nonbonded_function,
        combination_rule=combination_rule,
        gen_pairs=gen_pairs,
        vdw_14=scale_lj,
        coul_14=scale_electrostatics,
    )

    unique_molecule_map: Dict[
        int,
        List,
    ] = interchange.topology.identical_molecule_groups

    # Give each atom in each unique molecule a unique name so that can act like an atom type

    # TODO: Virtual sites
    _atom_atom_type_map: Dict["Atom", str] = dict()

    try:
        vdw_collection = interchange["vdW"]
        electrostatics_collection = interchange["Electrostatics"]
    except KeyError:
        raise UnsupportedExportError("Plugins not implemented.")

    for unique_molecule_index in unique_molecule_map:
        unique_molecule = interchange.topology.molecule(unique_molecule_index)

        if unique_molecule.name == "":
            unique_molecule.name = "MOL" + str(unique_molecule_index)

        for atom in unique_molecule.atoms:
            atom_type_name = f"{unique_molecule.name}{unique_molecule.atom_index(atom)}"
            _atom_atom_type_map[atom] = atom_type_name

            topology_index = interchange.topology.atom_index(atom)
            key = TopologyKey(atom_indices=(topology_index,))
            vdw_parameters = vdw_collection.potentials[
                vdw_collection.key_map[key]
            ].parameters
            charge = electrostatics_collection.charges[key]

            # Build atom types
            system.atom_types[atom_type_name] = LennardJonesAtomType(
                name=_atom_atom_type_map[atom],
                bonding_type="",
                atomic_number=atom.atomic_number,
                mass=MASSES[atom.atomic_number],
                charge=unit.Quantity(0.0, unit.elementary_charge),
                particle_type="A",
                sigma=vdw_parameters["sigma"].to(unit.nanometer),
                epsilon=vdw_parameters["epsilon"].to(unit.kilojoule_per_mole),
            )

            _atom_atom_type_map[atom] = atom_type_name

    for unique_molecule_index in unique_molecule_map:
        unique_molecule = interchange.topology.molecule(unique_molecule_index)

        if unique_molecule.name == "":
            unique_molecule.name = "MOL" + str(unique_molecule_index)

        molecule = GROMACSMolecule(name=unique_molecule.name)

        for atom in unique_molecule.atoms:
            unique_residue_names = {
                atom.metadata.get("residue_name", None)
                for atom in unique_molecule.atoms
            }

            if None in unique_residue_names:
                if len(unique_residue_names) > 1:
                    raise NotImplementedError(
                        "If some atoms have residue names, all atoms must have residue names.",
                    )
                else:
                    for atom in unique_molecule.atoms:
                        atom.metadata["residue_name"] = unique_molecule.name

            name = SYMBOLS[atom.atomic_number] if atom.name == "" else atom.name
            charge = (
                unit.Quantity(0.0, unit.elementary_charge)
                if atom.partial_charge is None
                else atom.partial_charge
            )

            molecule.atoms.append(
                GROMACSAtom(
                    index=unique_molecule.atom_index(atom) + 1,
                    name=name,
                    atom_type=_atom_atom_type_map[atom],
                    residue_index=atom.metadata.get(
                        "residue_number",
                        unique_molecule_index + 1,
                    ),
                    residue_name=atom.metadata["residue_name"],
                    charge_group_number=1,
                    charge=charge,
                    mass=MASSES[atom.atomic_number],
                ),
            )

        # Use a set to de-duplicate
        pairs: Set[Tuple] = {*_get_14_pairs(unique_molecule)}

        for pair in pairs:
            molecule_indices = sorted(unique_molecule.atom_index(atom) for atom in pair)

            if system.gen_pairs:
                molecule.pairs.append(
                    GROMACSPair(
                        atom1=molecule_indices[0] + 1,
                        atom2=molecule_indices[1] + 1,
                    ),
                )

            else:
                raise NotImplementedError()

        _convert_bonds(molecule, unique_molecule, interchange)
        _convert_angles(molecule, unique_molecule, interchange)
        # pairs
        _convert_dihedrals(molecule, unique_molecule, interchange)
        # settles?
        # constraints?

        system.molecule_types[unique_molecule.name] = molecule

        system.molecules[unique_molecule.name] = len(
            [
                molecule
                for molecule in interchange.topology.molecules
                if molecule.is_isomorphic_with(unique_molecule)
            ],
        )

    system.positions = interchange.positions
    system.box = interchange.box

    return system


def _convert_bonds(
    molecule: GROMACSMolecule,
    unique_molecule: "Molecule",
    interchange: Interchange,
):
    collection = interchange["Bonds"]

    for bond in unique_molecule.bonds:
        molecule_indices = tuple(
            sorted(unique_molecule.atom_index(a) for a in bond.atoms),
        )
        topology_indices = tuple(
            sorted(interchange.topology.atom_index(atom) for atom in bond.atoms),
        )

        found_match = False
        for top_key in collection.key_map:
            top_key: TopologyKey  # type: ignore[no-redef]
            if top_key.atom_indices == topology_indices:
                pot_key = collection.key_map[top_key]
                found_match = True
                break
            elif top_key.atom_indices == topology_indices[::-1]:
                pot_key = collection.key_map[top_key]
                found_match = True
                break
            else:
                found_match = False

        if not found_match:
            raise MissingBondError(
                f"Failed to find parameters for bond with topology indices {topology_indices}",
            )

        params = collection.potentials[pot_key].parameters

        molecule.bonds.append(
            GROMACSBond(
                atom1=molecule_indices[0] + 1,
                atom2=molecule_indices[1] + 1,
                function=1,
                length=params["length"].to(unit.nanometer),
                k=params["k"].to(unit.kilojoule_per_mole / unit.nanometer**2),
            ),
        )


def _convert_angles(
    molecule: GROMACSMolecule,
    unique_molecule: "Molecule",
    interchange: Interchange,
):
    collection = interchange["Angles"]

    for angle in unique_molecule.angles:
        topology_indices = tuple(interchange.topology.atom_index(a) for a in angle)
        molecule_indices = tuple(unique_molecule.atom_index(a) for a in angle)

        found_match = False
        for top_key in collection.key_map:
            top_key: TopologyKey  # type: ignore[no-redef]
            if top_key.atom_indices == topology_indices:
                pot_key = collection.key_map[top_key]
                found_match = True
                break
            else:
                found_match = False

        if not found_match:
            raise MissingAngleError(
                f"Failed to find parameters for angle with topology indices {topology_indices}",
            )

        params = collection.potentials[pot_key].parameters

        molecule.angles.append(
            GROMACSAngle(
                atom1=molecule_indices[0] + 1,
                atom2=molecule_indices[1] + 1,
                atom3=molecule_indices[2] + 1,
                angle=params["angle"].to(unit.degree),
                k=params["k"].to(unit.kilojoule_per_mole / unit.radian**2),
            ),
        )


def _convert_dihedrals(
    molecule: GROMACSMolecule,
    unique_molecule: "Molecule",
    interchange: Interchange,
):
    rb_torsion_handler: Optional["Collection"] = interchange.collections.get(
        "RBTorsions",
        None,
    )
    proper_torsion_handler: Optional["Collection"] = interchange.collections.get(
        "ProperTorsions",
        None,
    )
    improper_torsion_handler: Optional["Collection"] = interchange.collections.get(
        "ImproperTorsions",
        None,
    )

    # TODO: Ensure number of torsions written matches what is expected
    for proper in unique_molecule.propers:
        topology_indices = tuple(interchange.topology.atom_index(a) for a in proper)
        molecule_indices = tuple(unique_molecule.atom_index(a) for a in proper)

        if proper_torsion_handler:
            for top_key in proper_torsion_handler.key_map:
                if top_key.atom_indices[0] != topology_indices[0]:
                    continue
                if top_key.atom_indices[1] != topology_indices[1]:
                    continue
                if top_key.atom_indices[2] != topology_indices[2]:
                    continue
                if top_key.atom_indices[3] != topology_indices[3]:
                    continue
                if top_key.atom_indices == topology_indices:
                    pot_key = proper_torsion_handler.key_map[top_key]
                    params = proper_torsion_handler.potentials[pot_key].parameters

                    idivf = int(params["idivf"]) if "idivf" in params else 1

                    molecule.dihedrals.append(
                        PeriodicProperDihedral(
                            atom1=molecule_indices[0] + 1,
                            atom2=molecule_indices[1] + 1,
                            atom3=molecule_indices[2] + 1,
                            atom4=molecule_indices[3] + 1,
                            phi=params["phase"].to(unit.degree),
                            k=params["k"].to(unit.kilojoule_per_mole) / idivf,
                            multiplicity=int(params["periodicity"]),
                        ),
                    )

        if rb_torsion_handler:
            for top_key in rb_torsion_handler.key_map:
                if top_key.atom_indices[0] != topology_indices[0]:
                    continue
                if top_key.atom_indices[1] != topology_indices[1]:
                    continue
                if top_key.atom_indices[2] != topology_indices[2]:
                    continue
                if top_key.atom_indices[3] != topology_indices[3]:
                    continue
                if top_key.atom_indices == topology_indices:
                    pot_key = rb_torsion_handler.key_map[top_key]
                    params = rb_torsion_handler.potentials[pot_key].parameters

                    molecule.dihedrals.append(
                        RyckaertBellemansDihedral(
                            atom1=molecule_indices[0] + 1,
                            atom2=molecule_indices[1] + 1,
                            atom3=molecule_indices[2] + 1,
                            atom4=molecule_indices[3] + 1,
                            c0=params["C0"],
                            c1=params["C1"],
                            c2=params["C2"],
                            c3=params["C3"],
                            c4=params["C4"],
                            c5=params["C5"],
                        ),
                    )

    # TODO: Ensure number of torsions written matches what is expected
    if improper_torsion_handler:
        # Molecule/Topology.impropers lists the central atom **second** ...
        for improper in unique_molecule.smirnoff_impropers:
            topology_indices = tuple(
                interchange.topology.atom_index(a) for a in improper
            )
            # ... so the tuple must be modified to list the central atom **first**,
            # which is how the improper handler's slot map is built up
            indices_to_match = (
                topology_indices[1],
                topology_indices[0],
                topology_indices[2],
                topology_indices[3],
            )

            molecule_indices = tuple(unique_molecule.atom_index(a) for a in improper)

            # Now, indices_to_match has the central atom listed **first**,
            # but it's still listed second in molecule_indices

            for top_key in improper_torsion_handler.key_map:
                if top_key.atom_indices[0] != indices_to_match[0]:
                    continue
                if top_key.atom_indices[1] != indices_to_match[1]:
                    continue
                if top_key.atom_indices[2] != indices_to_match[2]:
                    continue
                if top_key.atom_indices[3] != indices_to_match[3]:
                    continue
                if indices_to_match == top_key.atom_indices:
                    key = improper_torsion_handler.key_map[top_key]
                    params = improper_torsion_handler.potentials[key].parameters

                    idivf = int(params["idivf"])

                    molecule.dihedrals.append(
                        PeriodicImproperDihedral(
                            atom1=molecule_indices[1] + 1,
                            atom2=molecule_indices[0] + 1,
                            atom3=molecule_indices[2] + 1,
                            atom4=molecule_indices[3] + 1,
                            phi=params["phase"].to(unit.degree),
                            k=params["k"].to(unit.kilojoule_per_mole) / idivf,
                            multiplicity=int(params["periodicity"]),
                        ),
                    )

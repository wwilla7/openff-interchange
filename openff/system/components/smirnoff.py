from typing import TYPE_CHECKING, Dict, Optional, Tuple, Type, TypeVar, Union

from openff.toolkit.typing.engines.smirnoff.forcefield import ForceField
from openff.toolkit.typing.engines.smirnoff.parameters import (
    ChargeIncrementModelHandler,
    ConstraintHandler,
    LibraryChargeHandler,
    ParameterHandler,
    vdWHandler,
)
from openff.units import unit
from pydantic import Field
from simtk import unit as omm_unit
from typing_extensions import Literal

from openff.system.components.potentials import Potential, PotentialHandler
from openff.system.models import DefaultModel, PotentialKey, TopologyKey
from openff.system.types import FloatQuantity
from openff.system.utils import get_partial_charges_from_openmm_system

kcal_mol = omm_unit.kilocalorie_per_mole
kcal_mol_angstroms = kcal_mol / omm_unit.angstrom ** 2
kcal_mol_radians = kcal_mol / omm_unit.radian ** 2

if TYPE_CHECKING:
    from openff.toolkit.topology.topology import Topology
    from openff.toolkit.typing.engines.smirnoff.parameters import (
        AngleHandler,
        BondHandler,
        ImproperTorsionHandler,
        ProperTorsionHandler,
    )

    from openff.system.components.mdtraj import OFFBioTop


T = TypeVar("T", bound="SMIRNOFFPotentialHandler")
T_ = TypeVar("T_", bound="PotentialHandler")


class SMIRNOFFPotentialHandler(PotentialHandler):
    def store_matches(
        self,
        parameter_handler: ParameterHandler,
        topology: Union["Topology", "OFFBioTop"],
    ) -> None:
        """
        Populate self.slot_map with key-val pairs of slots
        and unique potential identifiers

        """
        if self.slot_map:
            # TODO: Should the slot_map always be reset, or should we be able to partially
            # update it? Also Note the duplicated code in the child classes
            self.slot_map = dict()
        matches = parameter_handler.find_matches(topology)
        for key, val in matches.items():
            topology_key = TopologyKey(atom_indices=key)
            potential_key = PotentialKey(id=val.parameter_type.smirks)
            self.slot_map[topology_key] = potential_key

    @classmethod
    def from_toolkit(
        cls: Type[T],
        parameter_handler: T_,
        topology: "Topology",
    ) -> T:
        """
        Create a SMIRNOFFAngleHandler from toolkit data.

        """
        handler = cls()
        handler.store_matches(parameter_handler=parameter_handler, topology=topology)
        handler.store_potentials(parameter_handler=parameter_handler)

        return handler


class SMIRNOFFBondHandler(SMIRNOFFPotentialHandler):

    type: Literal["Bonds"] = "Bonds"
    expression: Literal["k/2*(r-length)**2"] = "k/2*(r-length)**2"

    def store_potentials(self, parameter_handler: "BondHandler") -> None:
        """
        Populate self.potentials with key-val pairs of unique potential
        identifiers and their associated Potential objects

        """
        if self.potentials:
            self.potentials = dict()
        for potential_key in self.slot_map.values():
            smirks = potential_key.id
            parameter_type = parameter_handler.get_parameter({"smirks": smirks})[0]
            potential = Potential(
                parameters={
                    "k": parameter_type.k,
                    "length": parameter_type.length,
                },
            )
            self.potentials[potential_key] = potential

    @classmethod
    def from_toolkit(
        cls: Type[T],
        topology: "Topology",
        bond_handler: "BondHandler",
        constraint_handler: Optional[T] = None,
    ) -> Tuple[T, Optional[T]]:
        """
        Create a SMIRNOFFBondHandler from toolkit data.

        """
        handler = cls(type="Bonds", expression="k/2*(r-length)**2")
        handler.store_matches(parameter_handler=bond_handler, topology=topology)
        handler.store_potentials(parameter_handler=bond_handler)

        if constraint_handler:
            constraints = SMIRNOFFConstraintHandler()
            constraints.store_constraints(
                parameter_handler=constraint_handler,
                topology=topology,
                bond_handler=bond_handler,
            )
        else:
            constraints = None  # type: ignore[assignment]

        return handler, constraints


class SMIRNOFFConstraintHandler(SMIRNOFFPotentialHandler):

    type: Literal["Constraints"] = "Constraints"
    expression: Literal[""] = ""
    slot_map: Dict[TopologyKey, PotentialKey] = dict()
    constraints: Dict[
        PotentialKey, bool
    ] = dict()  # should this be named potentials for consistency?

    def store_constraints(
        self,
        parameter_handler: ConstraintHandler,
        topology: "OFFBioTop",
        bond_handler: SMIRNOFFBondHandler = None,
    ) -> None:

        if self.slot_map:
            self.slot_map = dict()
        matches = parameter_handler.find_matches(topology)
        for key, match in matches.items():
            topology_key = TopologyKey(atom_indices=key)
            smirks = match.parameter_type.smirks
            distance = match.parameter_type.distance
            if distance is not None:
                # This constraint parameter is fully specified
                potential_key = PotentialKey(id=smirks)
                distance = match.parameter_type.distance
            else:
                # This constraint parameter depends on the BondHandler
                if not bond_handler:
                    from openff.system.exceptions import MissingParametersError

                    raise MissingParametersError(
                        f"Constraint with SMIRKS pattern {smirks} found with no distance "
                        "specified, and no corresponding bond parameters were found. The distance "
                        "of this constraint is not specified."
                    )
                # so use the same PotentialKey instance as the BondHandler
                potential_key = bond_handler.slot_map[topology_key]
                self.slot_map[topology_key] = potential_key
                distance = bond_handler.potentials[potential_key].parameters["length"]
            potential = Potential(
                parameters={
                    "distance": distance,
                }
            )
            self.constraints[potential_key] = potential  # type: ignore[assignment]


class SMIRNOFFAngleHandler(SMIRNOFFPotentialHandler):

    type: Literal["Angles"] = "Angles"
    expression: Literal["k/2*(theta-angle)**2"] = "k/2*(theta-angle)**2"

    def store_potentials(self, parameter_handler: "AngleHandler") -> None:
        """
        Populate self.potentials with key-val pairs of unique potential
        identifiers and their associated Potential objects

        """
        for potential_key in self.slot_map.values():
            smirks = potential_key.id
            # ParameterHandler.get_parameter returns a list, although this
            # should only ever be length 1
            parameter_type = parameter_handler.get_parameter({"smirks": smirks})[0]
            potential = Potential(
                parameters={
                    "k": parameter_type.k,
                    "angle": parameter_type.angle,
                },
            )
            self.potentials[potential_key] = potential

    @classmethod
    def from_toolkit(
        cls: Type[T],
        parameter_handler: "AngleHandler",
        topology: "Topology",
    ) -> T:
        """
        Create a SMIRNOFFAngleHandler from toolkit data.

        """
        handler = cls()
        handler.store_matches(parameter_handler=parameter_handler, topology=topology)
        handler.store_potentials(parameter_handler=parameter_handler)

        return handler


class SMIRNOFFProperTorsionHandler(SMIRNOFFPotentialHandler):

    type: Literal["ProperTorsions"] = "ProperTorsions"
    expression: Literal[
        "k*(1+cos(periodicity*theta-phase))"
    ] = "k*(1+cos(periodicity*theta-phase))"

    def store_matches(
        self,
        parameter_handler: "ProperTorsionHandler",
        topology: "OFFBioTop",
    ) -> None:
        """
        Populate self.slot_map with key-val pairs of slots
        and unique potential identifiers

        """
        if self.slot_map:
            self.slot_map = dict()
        matches = parameter_handler.find_matches(topology)
        for key, val in matches.items():
            n_terms = len(val.parameter_type.k)
            for n in range(n_terms):
                smirks = val.parameter_type.smirks
                topology_key = TopologyKey(atom_indices=key, mult=n)
                potential_key = PotentialKey(id=smirks, mult=n)
                self.slot_map[topology_key] = potential_key

    def store_potentials(self, parameter_handler: "ProperTorsionHandler") -> None:
        """
        Populate self.potentials with key-val pairs of unique potential
        identifiers and their associated Potential objects

        """
        for potential_key in self.slot_map.values():
            smirks = potential_key.id
            n = potential_key.mult
            parameter_type = parameter_handler.get_parameter({"smirks": smirks})[0]
            # n_terms = len(parameter_type.k)
            parameters = {
                "k": parameter_type.k[n],
                "periodicity": parameter_type.periodicity[n] * unit.dimensionless,
                "phase": parameter_type.phase[n],
                "idivf": parameter_type.idivf[n] * unit.dimensionless,
            }
            potential = Potential(parameters=parameters)
            self.potentials[potential_key] = potential

    @classmethod
    def _from_toolkit(
        cls: Type[T],
        parameter_handler: "ProperTorsionHandler",
        topology: "Topology",
    ) -> T:

        handler = cls()
        handler.store_matches(parameter_handler=parameter_handler, topology=topology)
        handler.store_potentials(parameter_handler=parameter_handler)

        return handler


class SMIRNOFFImproperTorsionHandler(SMIRNOFFPotentialHandler):

    type: Literal["ImproperTorsions"] = "ImproperTorsions"
    expression: Literal[
        "k*(1+cos(periodicity*theta-phase))"
    ] = "k*(1+cos(periodicity*theta-phase))"

    def store_matches(
        self, parameter_handler: "ImproperTorsionHandler", topology: "OFFBioTop"
    ) -> None:
        """
        Populate self.slot_map with key-val pairs of slots
        and unique potential identifiers

        """
        if self.slot_map:
            self.slot_map = dict()
        matches = parameter_handler.find_matches(topology)
        for key, val in matches.items():
            parameter_handler._assert_correct_connectivity(
                val,
                [
                    (0, 1),
                    (1, 2),
                    (1, 3),
                ],
            )
            n_terms = len(val.parameter_type.k)
            for n in range(n_terms):
                smirks = val.parameter_type.smirks
                topology_key = TopologyKey(atom_indices=key, mult=n)
                potential_key = PotentialKey(id=smirks, mult=n)
                self.slot_map[topology_key] = potential_key

    def store_potentials(self, parameter_handler: "ImproperTorsionHandler") -> None:
        """
        Populate self.potentials with key-val pairs of unique potential
        identifiers and their associated Potential objects

        """
        for potential_key in self.slot_map.values():
            smirks = potential_key.id
            n = potential_key.mult
            parameter_type = parameter_handler.get_parameter({"smirks": smirks})[0]
            parameters = {
                "k": parameter_type.k[n],
                "periodicity": parameter_type.periodicity[n] * unit.dimensionless,
                "phase": parameter_type.phase[n],
                "idivf": 3.0 * unit.dimensionless,
            }
            potential = Potential(parameters=parameters)
            self.potentials[potential_key] = potential

    @classmethod
    def _from_toolkit(
        cls: Type[T],
        improper_torsion_Handler: "ImproperTorsionHandler",
        topology: "Topology",
    ) -> T:

        handler = cls()
        handler.store_matches(
            parameter_handler=improper_torsion_Handler, topology=topology
        )
        handler.store_potentials(parameter_handler=improper_torsion_Handler)

        return handler


class SMIRNOFFvdWHandler(SMIRNOFFPotentialHandler):

    type: Literal["vdW"] = "vdW"
    expression: Literal[
        "4*epsilon*((sigma/r)**12-(sigma/r)**6)"
    ] = "4*epsilon*((sigma/r)**12-(sigma/r)**6)"
    method: Literal["cutoff", "pme"] = Field("cutoff")
    cutoff: FloatQuantity["angstrom"] = Field(  # type: ignore
        9.0 * unit.angstrom,
        description="The distance at which vdW interactions are truncated",
    )
    switch_width: FloatQuantity["angstrom"] = Field(  # type: ignore
        1.0 * unit.angstrom,  # type: ignore
        description="The width over which the switching function is applied",
    )
    scale_13: float = Field(
        0.0, description="The scaling factor applied to 1-3 interactions"
    )
    scale_14: float = Field(
        0.5, description="The scaling factor applied to 1-4 interactions"
    )
    scale_15: float = Field(
        1.0, description="The scaling factor applied to 1-5 interactions"
    )
    mixing_rule: Literal["Lorentz-Berthelot"] = Field(
        "Lorentz-Berthelot",
        description="The mixing rule (combination rule) used in computing pairwise vdW interactions",
    )

    def store_potentials(self, parameter_handler: vdWHandler) -> None:
        """
        Populate self.potentials with key-val pairs of unique potential
        identifiers and their associated Potential objects

        """
        self.method = parameter_handler.method.lower()
        self.cutoff = parameter_handler.cutoff

        for potential_key in self.slot_map.values():
            smirks = potential_key.id
            parameter_type = parameter_handler.get_parameter({"smirks": smirks})[0]
            try:
                potential = Potential(
                    parameters={
                        "sigma": parameter_type.sigma,
                        "epsilon": parameter_type.epsilon,
                    },
                )
            except AttributeError:
                # Handle rmin_half pending https://github.com/openforcefield/openff-toolkit/pull/750
                potential = Potential(
                    parameters={
                        "sigma": parameter_type.sigma,
                        "epsilon": parameter_type.epsilon,
                    },
                )
            self.potentials[potential_key] = potential


class SMIRNOFFElectrostaticsMetadataMixin(DefaultModel):

    type: Literal["Electrostatics"] = "Electrostatics"
    expression: Literal["coul"] = "coul"
    method: Literal["pme", "cutoff", "reaction-field"]
    cutoff: FloatQuantity["angstrom"] = 9.0  # type: ignore
    charge_map: Dict[TopologyKey, float] = dict()
    scale_13: float = Field(
        0.0, description="The scaling factor applied to 1-3 interactions"
    )
    scale_14: float = Field(
        0.8333333333, description="The scaling factor applied to 1-4 interactions"
    )
    scale_15: float = Field(
        1.0, description="The scaling factor applied to 1-5 interactions"
    )

    def store_charges(
        self,
        forcefield: ForceField,
        topology: "OFFBioTop",
    ) -> None:
        """
        Populate self.slot_map with key-val pairs of slots
        and unique potential identifiers

        """
        self.method = forcefield["Electrostatics"].method

        partial_charges = get_partial_charges_from_openmm_system(
            forcefield.create_openmm_system(topology=topology)
        )

        for i, charge in enumerate(partial_charges):
            topology_key = TopologyKey(atom_indices=(i,))
            self.charge_map[topology_key] = charge * unit.elementary_charge


class SMIRNOFFLibraryChargeHandler(  # type: ignore[misc]
    SMIRNOFFPotentialHandler,
):

    type: Literal["LibraryCharges"] = "LibraryCharges"
    # TODO: This should be specified by a parent class and not required (or event allowed)
    # to be specified here
    expression: Literal["coul"] = "coul"

    def store_potentials(self, parameter_handler: LibraryChargeHandler) -> None:
        if self.potentials:
            self.potentials = dict()
        for potential_key in self.slot_map.values():
            smirks = potential_key.id
            parameter_type = parameter_handler.get_parameter({"smirks": smirks})[0]
            charges_unitless = [val._value for val in parameter_type.charge]
            potential = Potential(
                parameters={"charges": charges_unitless * unit.elementary_charge},
            )
            self.potentials[potential_key] = potential


class SMIRNOFFChargeIncrementHandler(  # type: ignore[misc]
    SMIRNOFFPotentialHandler,
):

    type: Literal["ChargeIncrements"] = "ChargeIncrements"
    # TODO: This should be specified by a parent class and not required (or event allowed)
    # to be specified here
    expression: Literal["coul"] = "coul"
    partial_charge_method: str = "AM1-Mulliken"
    potentials: Dict[PotentialKey, Potential] = dict()

    def store_potentials(self, parameter_handler: ChargeIncrementModelHandler) -> None:
        if self.potentials:
            self.potentials = dict()
        for potential_key in self.slot_map.values():
            smirks = potential_key.id
            parameter_type = parameter_handler.get_parameter({"smirks": smirks})[0]
            charges_unitless = [val._value for val in parameter_type.charge_increment]
            potential = Potential(
                parameters={
                    "charge_increments": charges_unitless * unit.elementary_charge
                },
            )
            self.potentials[potential_key] = potential


class ElectrostaticsMetaHandler(SMIRNOFFElectrostaticsMetadataMixin):

    method: Literal["pme", "cutoff", "reaction-field"]
    charges: Dict = dict()  # type
    cache: Dict = dict()  # Dict[str: Dict[str, FloatQuantity["elementary_charge"]]]

    def cache_charges(self, partial_charge_method: str, topology: "OFFBioTop"):

        charges: Dict[TopologyKey, FloatQuantity] = dict()

        for ref_mol in topology.reference_molecules:
            ref_mol.assign_partial_charges(partial_charge_method=partial_charge_method)

            for top_mol in topology._reference_molecule_to_topology_molecules[ref_mol]:
                for topology_particle in top_mol.atoms:
                    ref_mol_particle_index = (
                        topology_particle.atom.molecule_particle_index
                    )
                    topology_particle_index = topology_particle.topology_particle_index
                    partial_charge = ref_mol._partial_charges[ref_mol_particle_index]
                    partial_charge = partial_charge.value_in_unit(
                        omm_unit.elementary_charge
                    )
                    partial_charge = partial_charge * unit.elementary_charge
                    top_key = TopologyKey(atom_indices=(topology_particle_index,))
                    charges[top_key] = partial_charge

        self.cache[partial_charge_method] = charges

    def apply_charge_increments(
        self, charge_increments: SMIRNOFFChargeIncrementHandler
    ):
        for top_key, pot_key in charge_increments.slot_map.items():
            ids = top_key.atom_indices
            charges = charge_increments.potentials[pot_key].parameters[
                "charge_increments"
            ]
            for i, id_ in enumerate(ids):
                atom_key = TopologyKey(atom_indices=(id_,))
                self.charges[atom_key] += charges[i]  # type: ignore

    def apply_library_charges(self, library_charges: SMIRNOFFLibraryChargeHandler):
        for top_key, pot_key in library_charges.slot_map.items():
            ids = top_key.atom_indices
            charges = library_charges.potentials[pot_key].parameters["charges"]
            # Need to ensure this iterator follows ordering in force field
            for i, id_ in enumerate(ids):
                atom_key = TopologyKey(atom_indices=(id_,))
                self.charges[atom_key] = charges[i]  # type: ignore

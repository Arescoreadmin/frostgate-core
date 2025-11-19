from dataclasses import dataclass

from api.schemas import Persona


@dataclass
class PersonaProfile:
    name: Persona
    # how aggressive to be on ambiguous threats
    aggression: float  # 0.0 = super conservative, 1.0 = very aggressive
    # minimum anomaly / rule score to escalate to "high"
    high_threshold: float


PERSONAS: dict[Persona, PersonaProfile] = {
    Persona.GUARDIAN: PersonaProfile(
        name=Persona.GUARDIAN,
        aggression=0.25,
        high_threshold=0.8,
    ),
    Persona.SENTINEL: PersonaProfile(
        name=Persona.SENTINEL,
        aggression=0.7,
        high_threshold=0.6,
    ),
}


def get_persona_profile(persona: Persona) -> PersonaProfile:
    return PERSONAS.get(persona, PERSONAS[Persona.GUARDIAN])

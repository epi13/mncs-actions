# Contract evolution

The contracts in this repository are deliberately experimental. Their purpose is to give the ecosystem a stable point to pressure with real repositories and workflows.

## Compatibility rules

- Additive fields are preferred when existing consumers can ignore them safely.
- A change to required fields, verdict meaning, digest meaning, or path semantics is breaking.
- Breaking changes require a new schema version and an explicit migration note.
- Actions should keep accepting the prior contract for a documented compatibility window when practical.
- A workflow must not promote an artifact solely because a parser ignored a field it did not understand.

## Pressure loop

~~~text
candidate implementation
        |
        v
workflow consumes contract
        |
        +--> contract holds
        |       |
        |       v
        |   preserve evidence
        |
        +--> capability gap
                |
                v
        record pressure and route to owner
~~~

A capability gap is evidence about the current language, standard, or integration surface. It is not permission to weaken the contract silently. Future actions may emit canonical capability-gap records once the owning MNCS-family contract is available.

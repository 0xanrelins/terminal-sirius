__all__ = ["SimulationEngine"]


def __getattr__(name: str):
    if name == "SimulationEngine":
        from simulation.engine import SimulationEngine

        return SimulationEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

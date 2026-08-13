class SpikeError(Exception):
    """Base class for deterministic spike failures."""


class TemplateValidationError(SpikeError):
    pass


class FixtureValidationError(SpikeError):
    pass


class RenderValidationError(SpikeError):
    pass


class ConversionError(SpikeError):
    pass


class OutputError(SpikeError):
    pass

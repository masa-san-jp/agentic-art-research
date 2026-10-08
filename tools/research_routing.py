"""Select research authoring explicitly; new research uses elements."""
import warnings
from _common import load_json, load_yaml


class LegacyResearchWarning(FutureWarning):
    pass


def select_route(protocol_root, project=None, *, declared=None, override=None, warn=True):
    if project is not None:
        declared = load_yaml(project / '01_planning/research-plan.yaml').get('research_route')
    config = load_yaml(protocol_root / 'config/research-elements.yaml')
    choices = load_json(protocol_root / 'schemas/common.schema.json')['$defs']['researchRoute']['enum']
    route = override if override is not None else declared if declared is not None else config['default_route']
    if route not in choices:
        raise ValueError(f'research_route: expected one of {choices}')
    if route == 'legacy' and warn:
        warnings.warn('Legacy research generates whole-role files (TASK001–009); explicitly selected compatibility mode will be removed after Stage B acceptance. Use research_elements for new research.', LegacyResearchWarning, stacklevel=2)
    return route

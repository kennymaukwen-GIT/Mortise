"""產物與修正單：七個階段之間交接的資料格式。名稱固定，不得改名。"""

from .common import *  # noqa: F401,F403
from .correction import Correction, CorrectionSource  # noqa: F401
from .element_list import *  # noqa: F401,F403
from .fidelity_report import *  # noqa: F401,F403
from .layout import *  # noqa: F401,F403
from .output_file import Degradation, OutputFile  # noqa: F401
from .page_spec import PageSpec  # noqa: F401
from .source_doc import FakeNativeFlags, Intent, PageRef, SourceDoc  # noqa: F401
from .styled_doc import *  # noqa: F401,F403

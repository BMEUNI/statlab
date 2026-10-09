"""Importing this package registers every tool module onto `statlab_tools.tool.server`.
Adding a tool is a decorated function in its phase's module plus one import line here -- never
a central list (§6.2)."""

from __future__ import annotations

from statlab_tools.tools import artifacts as artifacts
from statlab_tools.tools import champion as champion
from statlab_tools.tools import cleaning as cleaning
from statlab_tools.tools import encoding as encoding
from statlab_tools.tools import evaluation as evaluation
from statlab_tools.tools import features as features
from statlab_tools.tools import inference as inference
from statlab_tools.tools import intake as intake
from statlab_tools.tools import interpretation as interpretation
from statlab_tools.tools import profiling as profiling
from statlab_tools.tools import quality as quality
from statlab_tools.tools import reporting as reporting
from statlab_tools.tools import reproducibility as reproducibility
from statlab_tools.tools import splitting as splitting
from statlab_tools.tools import training as training

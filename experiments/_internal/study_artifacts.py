"""Back-compat shim: artifacts helpers now live in :mod:`silisocs.studies.study_artifacts`."""

from __future__ import annotations

import silisocs.studies.study_artifacts as _impl

globals().update({name: value for name, value in vars(_impl).items() if not name.startswith("__")})

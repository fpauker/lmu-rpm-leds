# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Translation setup, shared by the daemon, the app and the tools.

Source strings are English; German lives in po/de.po. Importing this module is
enough — it never raises, and with no catalogue installed every string falls
back to the English original.
"""

import gettext
import os

DOMAIN = "lmu-rpm-leds"


def _localedir():
    """Where the compiled catalogues are.

    A source checkout keeps them in ./locale (built by `make locale`); an
    installed copy has none there and falls through to the system default,
    which is /usr/share/locale.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, "locale")
    return local if os.path.isdir(local) else None


_translation = gettext.translation(DOMAIN, localedir=_localedir(), fallback=True)

# The conventional short name. Import as: from i18n import _
_ = _translation.gettext
ngettext = _translation.ngettext

# For words that mean different things in different places. "Start" is a button
# in one spot and an axis label in another, and one catalogue entry cannot serve
# both — the button reads "Starten" in German, the label must not.
pgettext = _translation.pgettext

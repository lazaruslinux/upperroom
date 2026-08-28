"""
The projector's modules import each other by bare name, the same way they do
when it runs from inside the projector dir, so put that dir on the path first.

Nothing here reaches the network, a library, or ffmpeg: these tests cover the
pure parts (the argv the encoder is given, the URLs the library is asked for,
and the protocol envelope), which are exactly the parts whose mistakes are
invisible until a broadcast is already going badly.
"""

import os
import sys

PROJECTOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECTOR_DIR)

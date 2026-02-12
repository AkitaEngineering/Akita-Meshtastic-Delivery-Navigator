import py_compile
from pathlib import Path
import sys

files = list(Path('.').rglob('*.py'))
count = 0
try:
    for f in files:
        py_compile.compile(str(f), doraise=True)
        count += 1
    print('Compiled', count, 'files')
except Exception as e:
    print('ERROR compiling:', f, e)
    sys.exit(2)

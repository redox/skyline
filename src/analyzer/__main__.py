import sys
import json
import algorithms
from algorithm_exceptions import *

if len(sys.argv) != 2:
  print(("usage: %s /path/to/serie.json" % sys.argv[0]), file=sys.stderr)
  sys.exit(1)

with open(sys.argv[1]) as data_file: 
  data = json.load(data_file)

if data['serie'] is None:
  print(("usage: %s /path/to/serie.json" % sys.argv[0]), file=sys.stderr)
  sys.exit(2)

try:
  sys.exit(1 if algorithms.run_selected_algorithm(data['serie']) else 0)
except TooShort:
  print("Too short")
  sys.exit(0)
except Boring:
  print("Boring")
  sys.exit(0)
except Stale:
  print("Stale")
  sys.exit(0)

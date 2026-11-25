import argparse

parser = argparse.ArgumentParser()
parser.add_argument("-n", "--iters", type=int, default=10)
parser.add_argument("--mode", choices=["fast", "full"], default="full")
parser.add_argument("--fast", action="store_true")
parser.add_argument("--offset", default=-1)
parser.add_argument("--files", nargs="+")
parser.add_argument("input")

#!/usr/bin/env python3
"""Minimal evaluator for ae_agent_smoke: output 1 if success.txt exists and contains '1', else 0.

Output must be a single digit on a line (or last line) for benchmark score parsing.
"""
import os
import sys

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "success.txt")
    if os.path.isfile(path):
        with open(path, "r") as f:
            content = f.read().strip()
        if content == "1":
            print(1)
            sys.exit(0)
    print(0)
    sys.exit(0)

if __name__ == "__main__":
    main()

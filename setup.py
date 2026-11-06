from setuptools import find_namespace_packages, setup

setup(packages=[*find_namespace_packages("src"), "runtime.builtin_agents"])

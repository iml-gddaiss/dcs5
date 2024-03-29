
from setuptools import find_packages, setup
from dcs5 import VERSION

setup(
    name="dcs5",
    version=VERSION,
    author="JeromeJGuay",
    author_email="jerome.guay@dfo-mpo.gc.ca",
    description="""BigFin Dcs5 Board Controller App.""",
    long_description_content_type="text/markdown",
    url="https://github.com/JeromeJGuay/dcs5",
    packages=find_packages(),
    package_data={"": ["default_configs/*.json", "static/*"]},
    include_package_data=True,
    classifiers=["Programming Language :: Python :: 3"],
    python_requires="~=3.10",
)


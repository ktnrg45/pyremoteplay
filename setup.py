#!/usr/bin/env python
"""Setup for pyremoteplay."""

from setuptools import find_packages, setup

VERSION = "0.0.2"

MIN_PY_VERSION = "3.8"

REQUIRES = list(open('requirements.txt'))
REQUIRES_GUI = list(open('requirements-gui.txt'))

CLASSIFIERS = [
    'Development Status :: 4 - Beta',
    'Environment :: Console',
    'Environment :: Console :: Curses',
    'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
    'Natural Language :: English',
    'Operating System :: OS Independent',
    'Programming Language :: Python :: 3.8',
    'Topic :: Games/Entertainment',
    'Topic :: Home Automation',
    'Topic :: Software Development :: Libraries :: Python Modules',
    'Topic :: System :: Hardware',
]

with open('README.md') as f:
    readme = f.read()

setup(
    name='pyremoteplay',
    version=VERSION,
    description='Remote Play Library',
    long_description=readme,
    long_description_content_type='text/markdown',
    author='ktnrg45',
    author_email='ktnrg45@github.com',
    packages=find_packages(exclude=['tests']),
    url='https://github.com/ktnrg45/pyps4-2ndscreen',
    license='GPLv3',
    classifiers=CLASSIFIERS,
    keywords='playstation sony ps4 ps5 remote play remoteplay rp',
    install_requires=REQUIRES,
    extras_require={"GUI": REQUIRES_GUI},
    python_requires='>={}'.format(MIN_PY_VERSION),
    test_suite='tests',
    include_package_data=True,
    entry_points={"console_scripts": [
        "pyremoteplay = pyremoteplay.__main__:main",
        "pyremoteplay-gui = pyremoteplay.gui:gui [GUI]",
    ]}
)

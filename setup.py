from setuptools import setup

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='sunnybeamtool',
    version='0.1.0',    
    description='SMA Sunny Beam Tool',
    url='https://github.com/dannerph/SunnyBeamToolPython',
    author='Philipp Danner',
    author_email='philipp@danner-web.de',
    long_description=long_description,
    long_description_content_type="text/markdown",
    license='GNU Lesser General Public License v3 (LGPLv3)',
    packages=['sunnybeamtool'],
    install_requires=['crcmod>=1.7',
                      'pyusb>=1.1.1',                    
                      ],

    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
        "Operating System :: OS Independent",
    ],
)

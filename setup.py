from setuptools import setup, find_packages

setup(
    name='macrorec',
    version='1.0.0',
    description='Record and replay mouse + keyboard input with precise timing',
    packages=find_packages(),
    python_requires='>=3.8',
    install_requires=['pynput>=1.7.6'],
    entry_points={
        'console_scripts': ['macrorec=macrorec.__main__:main'],
    },
)

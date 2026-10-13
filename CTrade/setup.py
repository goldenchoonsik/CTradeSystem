from setuptools import setup, Extension
import pybind11

ext_modules = [
    Extension(
        'ctrade_core',
        ['core/cpp/ctrade_core.cpp'],
        include_dirs=[
            pybind11.get_include(),
            'core/cpp',
        ],
        language='c++',
        extra_compile_args=['-std=c++17', '-O3'],
    ),
]

setup(
    name='ctrade_core',
    version='1.0.0',
    ext_modules=ext_modules,
)

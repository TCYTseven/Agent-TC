import re

from setuptools import find_packages, setup

# PyPI cannot resolve the README's relative links, so point them at GitHub.
long_description = re.sub(
    r'(\]\(|href=")\./', r"\1https://github.com/TCYTseven/Agent-TC/blob/main/",
    open("README.md", encoding="utf-8").read(),
)

setup(
    name="agent-tc",
    version="0.4.0",
    description="Tell your computer what to do in plain English; a fast computer-use agent.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Simular AI",
    author_email="eric@simular.ai",
    # agent_s4 is a separate distribution (agent_s4/pyproject.toml).
    packages=find_packages(include=["gui_agents", "gui_agents.*"]),
    install_requires=[
        "anthropic",
        "backoff",
        "mss",
        "numpy",
        "openai",
        "pillow",
        "pyautogui",
        "pytesseract",
        'pyobjc; platform_system == "Darwin"',
        'pywinauto; platform_system == "Windows"',
        'pywin32; platform_system == "Windows"',
    ],
    # bbon (Behavior Best-of-N judging) needs OpenCV
    extras_require={"dev": ["black"], "bbon": ["opencv-python"]},
    entry_points={
        "console_scripts": [
            "agent-tc=gui_agents.s3.cli_app:main",
            "agent_s=gui_agents.s3.cli_app:main",  # old name
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS :: MacOS X",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    keywords="ai, llm, gui, agent, multimodal",
    project_urls={
        "Source": "https://github.com/TCYTseven/Agent-TC",
        "Bug Reports": "https://github.com/TCYTseven/Agent-TC/issues",
    },
    python_requires=">=3.9",
)

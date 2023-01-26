@echo off

call venv\Scripts\activate.bat
echo.
echo ISORT
echo ----------------------------------------
isort .\ --sp=.isort.cfg
echo ----------------------------------------
echo.
echo BLACK
echo ----------------------------------------
black .\ --config=pyproject.toml
echo ----------------------------------------
echo.

call venv\Scripts\deactivate.bat

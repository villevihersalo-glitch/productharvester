@echo off
setlocal
cd /d "%~dp0"

rem Keep the harvested data outside the GitHub project folder.
if not defined PRODUCT_HARVESTER_DATA_DIR set "PRODUCT_HARVESTER_DATA_DIR=%USERPROFILE%\Documents\ProductHarvesterData"

if not exist "%PRODUCT_HARVESTER_DATA_DIR%\data\catalog_registry_v69.sqlite" (
    echo ERROR: Product Harvester database not found:
    echo "%PRODUCT_HARVESTER_DATA_DIR%\data\catalog_registry_v69.sqlite"
    echo.
    echo Restore the backed-up database to the folder above.
    echo No empty database will be created by this launcher.
    pause
    exit /b 1
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 run.py --data-dir "%PRODUCT_HARVESTER_DATA_DIR%"
) else (
    python run.py --data-dir "%PRODUCT_HARVESTER_DATA_DIR%"
)
pause

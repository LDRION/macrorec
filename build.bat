@echo off
echo.
echo  Сборка MacroRec.exe...
echo.

pip install pyinstaller customtkinter pynput --quiet

pyinstaller ^
  --onefile ^
  --windowed ^
  --name MacroRec ^
  --collect-all customtkinter ^
  --hidden-import pynput.keyboard ^
  --hidden-import pynput.mouse ^
  --hidden-import pynput._util.win32 ^
  app.py

echo.
echo  Готово! Файл: dist\MacroRec.exe
echo.
pause

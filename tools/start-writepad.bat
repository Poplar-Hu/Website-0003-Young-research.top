@echo off
REM ===========================================================================
REM  科研记录写字板 —— 启动器
REM  双击本文件即可；也可以拖一个项目路径进来：start-writepad.bat example-record
REM ===========================================================================

REM 控制台切到 UTF-8，否则 Python 输出的中文在这个窗口里是乱码
chcp 65001 >nul

REM 切到仓库根目录：生成器要按仓库根的相对位置找 content/ 和 assets/
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   没有找到 python 命令。
  echo.
  echo   请先安装 Python 3.10 或更高版本，安装时记得勾选
  echo   "Add python.exe to PATH"，然后重新双击本文件。
  echo.
  pause
  exit /b 1
)

python "%~dp0writepad.py" %*

if errorlevel 1 (
  echo.
  echo   写字板异常退出了，错误信息见上。
  echo.
  pause
)

@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo ======================================
echo  启动 SVM 材质识别系统...
echo ======================================
python "test_compare_svm.py"
pause
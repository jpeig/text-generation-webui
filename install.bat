@echo off

@rem Based on the installer found here: https://github.com/Sygil-Dev/sygil-webui
@rem This script will install git and all dependencies
@rem using micromamba (an 8mb static-linked single-file binary, conda replacement).
@rem This enables a user to install this project without manually installing conda and git.

echo What is your GPU?
echo.
echo A) NVIDIA
echo B) None (I want to run in CPU mode)
echo.
set /p "gpuchoice=Input> "
set gpuchoice=%gpuchoice:~0,1%

if /I "%gpuchoice%" == "A" (
    set "PACKAGES_TO_INSTALL=python=3.10.9 pytorch torchvision torchaudio pytorch-cuda=11.7 cuda-toolkit conda-forge::ninja conda-forge::git"
    set "CHANNEL=-c pytorch -c nvidia/label/cuda-11.7.0 -c nvidia"
) else if /I "%gpuchoice%" == "B" (
    set "PACKAGES_TO_INSTALL=pytorch torchvision torchaudio cpuonly git"
    set "CHANNEL=-c conda-forge -c pytorch"
) else (
    echo Invalid choice. Exiting...
    exit
)

cd /D "%~dp0"

set MAMBA_ROOT_PREFIX=%cd%\installer_files\mamba
set INSTALL_ENV_DIR=%cd%\installer_files\env
set MICROMAMBA_DOWNLOAD_URL=https://github.com/cmdr2/stable-diffusion-ui/releases/download/v1.1/micromamba.exe
set REPO_URL=https://github.com/oobabooga/text-generation-webui.git
set umamba_exists=F

@rem figure out whether git and conda needs to be installed
call "%MAMBA_ROOT_PREFIX%\micromamba.exe" --version >nul 2>&1
if "%ERRORLEVEL%" EQU "0" set umamba_exists=T

@rem (if necessary) install git and conda into a contained environment
if "%PACKAGES_TO_INSTALL%" NEQ "" (
    @rem download micromamba
    if "%umamba_exists%" == "F" (
        echo "Downloading Micromamba from %MICROMAMBA_DOWNLOAD_URL% to %MAMBA_ROOT_PREFIX%\micromamba.exe"

        mkdir "%MAMBA_ROOT_PREFIX%"
        call curl -L "%MICROMAMBA_DOWNLOAD_URL%" > "%MAMBA_ROOT_PREFIX%\micromamba.exe"

        @rem test the mamba binary
        echo Micromamba version:
        call "%MAMBA_ROOT_PREFIX%\micromamba.exe" --version || ( echo Micromamba not found. && goto end )
    )

    @rem create micromamba hook
    if not exist "%MAMBA_ROOT_PREFIX%\condabin\mamba_hook.bat" (
      call "%MAMBA_ROOT_PREFIX%\micromamba.exe" shell hook >nul 2>&1
    )

    @rem activate base micromamba env
    call "%MAMBA_ROOT_PREFIX%\condabin\mamba_hook.bat" || ( echo Micromamba hook not found. && goto end )

    @rem create the installer env
    if not exist "%INSTALL_ENV_DIR%" (
        call micromamba create -y --prefix "%INSTALL_ENV_DIR%"
    )
    @rem activate installer env
    call micromamba activate "%INSTALL_ENV_DIR%" || ( echo %INSTALL_ENV_DIR% not found. && goto end )

    echo "Packages to install: %PACKAGES_TO_INSTALL%"

    call micromamba install -y %CHANNEL% %PACKAGES_TO_INSTALL%
)

@rem clone the repository and install the pip requirements
if exist text-generation-webui\ (
  cd text-generation-webui
  git pull
) else (
  git clone https://github.com/oobabooga/text-generation-webui.git
  cd text-generation-webui || goto end
)
call python -m pip install -r requirements.txt --upgrade
call python -m pip install -r extensions\api\requirements.txt --upgrade
call python -m pip install -r extensions\elevenlabs_tts\requirements.txt --upgrade
call python -m pip install -r extensions\google_translate\requirements.txt --upgrade
call python -m pip install -r extensions\silero_tts\requirements.txt --upgrade
call python -m pip install -r extensions\whisper_stt\requirements.txt --upgrade

@rem skip gptq install if cpu only
if /I not "%gpuchoice%" == "A" goto bandaid

@rem download gptq and compile locally and if compile fails, install from wheel
if not exist repositories\ (
  mkdir repositories
)
cd repositories || goto end
if not exist GPTQ-for-LLaMa\ (
  git clone https://github.com/qwopqwop200/GPTQ-for-LLaMa.git
  cd GPTQ-for-LLaMa || goto end
  git reset --hard 468c47c01b4fe370616747b6d69a2d3f48bab5e4
  pip install -r requirements.txt
  call python setup_cuda.py install
  if not exist "%INSTALL_ENV_DIR%\lib\site-packages\quant_cuda-0.0.0-py3.10-win-amd64.egg" (
    echo CUDA kernal compilation failed. Will try to install from wheel.
    pip install unzip
    curl -LO https://github.com/oobabooga/text-generation-webui/files/11023775/quant_cuda-0.0.0-cp310-cp310-win_amd64.whl.zip
    unzip quant_cuda-0.0.0-cp310-cp310-win_amd64.whl.zip
    pip install quant_cuda-0.0.0-cp310-cp310-win_amd64.whl || ( echo Wheel installation failed. && goto end )
  )
  cd ..
)
cd ..\..

:bandaid
curl -LO https://github.com/DeXtmL/bitsandbytes-win-prebuilt/raw/main/libbitsandbytes_cpu.dll
curl -LO https://github.com/james-things/bitsandbytes-prebuilt-all_arch/raw/main/0.37.0/libbitsandbytes_cudaall.dll
mv libbitsandbytes_cpu.dll "%INSTALL_ENV_DIR%\lib\site-packages\bitsandbytes"
mv libbitsandbytes_cuda116.dll "%INSTALL_ENV_DIR%\lib\site-packages\bitsandbytes"
pip install sed
sed -i "s/if not torch.cuda.is_available(): return 'libsbitsandbytes_cpu.so', None, None, None, None/if torch.cuda.is_available(): return 'libbitsandbytes_cudaall.dll', None, None, None, None\n    else: return 'libbitsandbytes_cpu.dll', None, None, None, None/g" "%INSTALL_ENV_DIR%\lib\site-packages\bitsandbytes\cuda_setup\main.py"
sed -i "s/ct.cdll.LoadLibrary(binary_path)/ct.cdll.LoadLibrary(str(binary_path))/g" "%INSTALL_ENV_DIR%\lib\site-packages\bitsandbytes\cuda_setup\main.py"

:end
pause

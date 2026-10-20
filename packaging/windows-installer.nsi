Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
Name "金融计划收费系统"
OutFile "@OUTPUT@"
InstallDir "$LOCALAPPDATA\FinancialFeeSystem\App"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "@VERSION@.0"
VIAddVersionKey /LANG=2052 "ProductName" "金融计划收费系统"
VIAddVersionKey /LANG=2052 "FileDescription" "金融计划收费系统空白安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "@VERSION@"
VIAddVersionKey /LANG=2052 "LegalCopyright" ""
!define MUI_WELCOMEPAGE_TEXT "安装金融计划收费系统空白版本。$\r$\n$\r$\n安装包不包含客户资料。首次启动请选择业务数据保存目录，之后更新程序会继续使用该目录。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!define MUI_UNCONFIRMPAGE_TEXT_TOP "卸载将移除程序文件，保留业务数据及数据目录配置。"
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
Var NoIntegration

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "本安装包需要64位Windows系统。"
    Abort
  ${EndIf}
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/NOINTEGRATION" $1
  IfErrors +2
    StrCpy $NoIntegration "1"
FunctionEnd

Section "安装"
  SetShellVarContext current
@INSTALL@
  WriteUninstaller "$INSTDIR\卸载程序.exe"
  StrCmp $NoIntegration "1" done
  CreateDirectory "$SMPROGRAMS\金融计划收费系统"
  CreateShortcut "$SMPROGRAMS\金融计划收费系统\金融计划收费系统.lnk" "$INSTDIR\FinancialFeeSystem.exe"
  CreateShortcut "$DESKTOP\金融计划收费系统.lnk" "$INSTDIR\FinancialFeeSystem.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem" "DisplayName" "金融计划收费系统"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem" "DisplayVersion" "@VERSION@"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem" "UninstallString" '"$INSTDIR\卸载程序.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem" "NoRepair" 1
  done:
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem" "InstallLocation"
  StrCmp $0 $INSTDIR 0 keepIntegration
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FinancialFeeSystem"
  Delete "$DESKTOP\金融计划收费系统.lnk"
  Delete "$SMPROGRAMS\金融计划收费系统\金融计划收费系统.lnk"
  RMDir "$SMPROGRAMS\金融计划收费系统"
  keepIntegration:
  ; Remove only files explicitly shipped by this installer. Never recurse through user data.
@UNINSTALL@
  Delete "$INSTDIR\卸载程序.exe"
  RMDir "$INSTDIR"
SectionEnd

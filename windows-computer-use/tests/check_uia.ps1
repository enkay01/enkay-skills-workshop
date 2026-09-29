Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$script = @"
import tkinter as tk
r = tk.Tk()
r.title('WCU_Tk_Fixture')
tk.Button(r, text='Continue', name='continue_btn').pack()
tk.Entry(r, name='text_field').pack()
r.after(6000, r.destroy)
r.mainloop()
"@

$proc = Start-Process python -ArgumentList "-c", "`"$script`"" -PassThru
Start-Sleep -Milliseconds 1500

$def = @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
'@
$win32 = Add-Type -MemberDefinition $def -Name Native -Namespace Win -PassThru
$hwnd = $win32::FindWindow($null, "WCU_Tk_Fixture")
Write-Host "Found HWND: $hwnd"

if ($hwnd -ne [IntPtr]::Zero) {
    $element = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
    Write-Host "Root element: '$($element.Current.Name)' Class: '$($element.Current.ClassName)'"
    $all = $element.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    Write-Host "Descendants count: $($all.Count)"
    foreach ($item in $all) {
        Write-Host "  Control: '$($item.Current.Name)' Class: '$($item.Current.ClassName)' Type: $($item.Current.ControlType.ProgrammaticName) AutoId: '$($item.Current.AutomationId)'"
        $patterns = $item.GetSupportedPatterns()
        foreach ($p in $patterns) {
            Write-Host "    Pattern: $($p.ProgrammaticName)"
        }
    }
}
$proc.WaitForExit()

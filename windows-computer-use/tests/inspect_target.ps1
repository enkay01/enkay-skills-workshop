param([string]$hwnd_str)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$hwnd = [IntPtr][int64]$hwnd_str
Write-Host "Inspecting HWND: $hwnd"

$element = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
if ($element -ne $null) {
    Write-Host "Root: '$($element.Current.Name)' Class: '$($element.Current.ClassName)'"
    $all = $element.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    Write-Host "Descendants count: $($all.Count)"
    foreach ($item in $all) {
        Write-Host "  Name: '$($item.Current.Name)' Class: '$($item.Current.ClassName)' ControlType: $($item.Current.ControlType.ProgrammaticName) AutoId: '$($item.Current.AutomationId)'"
        $patterns = $item.GetSupportedPatterns()
        foreach ($pat in $patterns) {
            Write-Host "    Pattern: $($pat.ProgrammaticName)"
        }
    }
} else {
    Write-Host "Element is null"
}

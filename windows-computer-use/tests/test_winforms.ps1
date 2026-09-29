Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$form = New-Object System.Windows.Forms.Form
$form.Text = "WinForms_Fixture"
$btn = New-Object System.Windows.Forms.Button
$btn.Text = "Continue"
$btn.Name = "btn_continue"
$txt = New-Object System.Windows.Forms.TextBox
$txt.Text = "Initial Value"
$txt.Name = "txt_input"
$form.Controls.Add($btn)
$form.Controls.Add($txt)

$form.Show()
$hwnd = $form.Handle
Write-Host "WinForms HWND: $hwnd"

$element = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
$all = $element.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
Write-Host "Descendants count: $($all.Count)"
foreach ($item in $all) {
    Write-Host "  Name: '$($item.Current.Name)' Class: '$($item.Current.ClassName)' Type: $($item.Current.ControlType.ProgrammaticName) AutoId: '$($item.Current.AutomationId)'"
    foreach ($p in $item.GetSupportedPatterns()) {
        Write-Host "    Pattern: $($p.ProgrammaticName)"
    }
}
$form.Close()

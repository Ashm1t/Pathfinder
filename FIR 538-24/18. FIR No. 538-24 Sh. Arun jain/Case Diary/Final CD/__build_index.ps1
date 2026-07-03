$ErrorActionPreference='"'"'Stop'"'"';
$root = (Get-Location).Path
$txtDir = Join-Path $root '__txt'
$csvPath = Join-Path $root '__index.csv'
$jsonPath = Join-Path $root '__summary.json'

$culture = [System.Globalization.CultureInfo]::GetCultureInfo('en-GB')
$events = @()
$nameCounts = @{}
$sectionCounts = @{}
$tagsSet = @{}

$keywordTags = @{
  'arrest'='Arrest';
  'bail'='Bail';
  'remand'='Remand';
  'judicial custody'='Judicial Custody';
  'j.c'='Judicial Custody';
  'jc'='Judicial Custody';
  'police custody'='Police Custody';
  'p.c'='Police Custody';
  'pc'='Police Custody';
  'notice'='Notice';
  'seiz'='Seizure';
  'search'='Search';
  'interrogation'='Interrogation';
  'disclosure'='Disclosure';
  'recover'='Recovery';
  'freeze'='Freeze';
  'defreeze'='Defreeze';
  'bank'='Bank';
  'kyc'='KYC';
  'gmail'='Gmail';
  'email'='Email';
  'cdr'='CDR';
  'transit'='Transit';
  'out station'='Outstation';
  'outstation'='Outstation';
  'bound down'='Bound Down';
  'complainant'='Complainant';
  'accused'='Accused';
  'challan'='Challan';
  'charge sheet'='Charge Sheet'
}

function Parse-Filename([string]$name){
  $m=[regex]::Match($name,'^CD No\.\s*(?<no>\d+)\s+dated\s+(?<date>\d{1,2}\.\d{1,2}\.\s*\d{2})(?:\s+(?<title>.*))?$', 'IgnoreCase')
  if($m.Success){
    $cd=[int]$m.Groups['no'].Value
    $dateRaw=($m.Groups['date'].Value -replace '\s','')
    $title=($m.Groups['title'].Value).Trim()
    return [pscustomobject]@{ CDNo=$cd; DateRaw=$dateRaw; Title=$title }
  }
  else {
    return [pscustomobject]@{ CDNo=$null; DateRaw=$null; Title=$name }
  }
}

function Normalize-Date([string]$d){
  if([string]::IsNullOrWhiteSpace($d)){ return $null }
  $parsed=[datetime]::MinValue
  if([datetime]::TryParseExact($d,'d.M.yy',$culture,[System.Globalization.DateTimeStyles]::None,[ref]$parsed)){
    return $parsed
  }
  return $null
}

function Extract-Sections([string]$text){
  $secs=@()
  foreach($m in ([regex]::Matches($text,'\b([1-9][0-9]{1,2}[A-Z]?)\s*IPC\b', 'IgnoreCase'))){ $secs += ($m.Groups[1].Value.ToUpper()+' IPC') }
  foreach($m in ([regex]::Matches($text,'\b([1-9][0-9]{1,2}[A-Z]?)\s*CrPC\b', 'IgnoreCase'))){ $secs += ($m.Groups[1].Value.ToUpper()+' CrPC') }
  foreach($m in ([regex]::Matches($text,'\bIT\s*Act\b', 'IgnoreCase'))){ $secs += 'IT Act' }
  return ($secs | Select-Object -Unique)
}

function Extract-Names([string]$text){
  $names=@()
  foreach($m in ([regex]::Matches($text,'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b'))){
    $n=$m.Groups[1].Value.Trim()
    if($n.Length -ge 4 -and $n -notmatch '^(Police|Station|Court|Delhi|FIR|Bank|Account|Mobile|Email|Address|Officer|Inspector|Sub|Head|Constable|HC|SI|ASI|SHO|IO|U/S|Under|Section|Date|Time|Place|No|CD|Accused|Complainant|Witness)$'){
      $names += $n
    }
  }
  return ($names | Group-Object | Sort-Object Count -Descending | Select-Object -First 10 -ExpandProperty Name)
}

function Detect-Tags([string]$text, [string]$title){
  $tags=@()
  $hay = ($title + ' ' + $text)
  $hayLower = $hay.ToLowerInvariant()
  foreach($k in $keywordTags.Keys){ if($hayLower -like ('*' + $k + '*')){ $tags += $keywordTags[$k] } }
  return ($tags | Select-Object -Unique)
}

$rows=@()
Get-ChildItem -Path $txtDir -Filter '*.txt' | ForEach-Object {
  $name=$_.BaseName
  $meta=Parse-Filename $name
  $text = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
  $date=Normalize-Date $meta.DateRaw
  $dateIso= if($date){ $date.ToString('yyyy-MM-dd') } else { $null }
  $sections=Extract-Sections $text
  foreach($s in $sections){ if($sectionCounts.ContainsKey($s)){ $sectionCounts[$s]++ } else { $sectionCounts[$s]=1 } }
  $topNames=Extract-Names $text
  foreach($n in $topNames){ if($nameCounts.ContainsKey($n)){ $nameCounts[$n]++ } else { $nameCounts[$n]=1 } }
  $tags=Detect-Tags $text $meta.Title
  foreach($t in $tags){ $tagsSet[$t]=$true }
  $firstLine = ($text -split "\r?\n")[0]
  $rows += [pscustomobject]@{
    File=$_.Name; CDNo=$meta.CDNo; Date=$dateIso; Title=$meta.Title; Tags=($tags -join '; '); Sections=($sections -join '; '); Snippet=$firstLine
  }
  $events += [pscustomobject]@{ CDNo=$meta.CDNo; Date=$dateIso; Title=$meta.Title; Tags=$tags }
}

$rows | Sort-Object {[int]($_.CDNo)} | Export-Csv -NoTypeInformation -Path $csvPath -Encoding UTF8

$topSections = $sectionCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 10
$topNamesList = $nameCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 15
$allTags = $tagsSet.Keys | Sort-Object

$chron = $events | Sort-Object @{Expression='Date';Descending=$false}, @{Expression='CDNo';Descending=$false} | ForEach-Object { [pscustomobject]@{ CDNo=$_.CDNo; Date=$_.Date; Title=$_.Title; Tags=($_.Tags -join ', ') } }

$summary = [pscustomobject]@{
  Counts = [pscustomobject]@{ Documents=$rows.Count; DistinctTags=$allTags.Count; DistinctSections=$sectionCounts.Keys.Count }
  TopSections = ($topSections | ForEach-Object { [pscustomobject]@{ Section=$_.Key; Count=$_.Value } })
  TopNames = ($topNamesList | ForEach-Object { [pscustomobject]@{ Name=$_.Key; Mentions=$_.Value } })
  Tags = $allTags
  Chronology = $chron
}

$summary | ConvertTo-Json -Depth 6 | Set-Content -Path $jsonPath -Encoding UTF8

"CSV:`t$csvPath"
"JSON:`t$jsonPath"
$chron | Select-Object -First 20 | Format-Table -AutoSize | Out-String

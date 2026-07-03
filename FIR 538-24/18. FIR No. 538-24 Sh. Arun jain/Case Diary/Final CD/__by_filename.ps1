Continue = 'Stop'
 = [System.Globalization.CultureInfo]::GetCultureInfo('en-GB')
='__index_by_filename.csv'
='__summary_by_filename.json'

 = @{
  'arrest'='Arrest'; 'bail'='Bail'; 'remand'='Remand'; 'judicial custody'='Judicial Custody'; 'j.c'='Judicial Custody'; 'jc'='Judicial Custody'; 'police custody'='Police Custody'; 'p.c'='Police Custody'; 'pc'='Police Custody'; 'notice'='Notice'; 'seiz'='Seizure'; 'search'='Search'; 'interrogation'='Interrogation'; 'disclosure'='Disclosure'; 'recover'='Recovery'; 'freeze'='Freeze'; 'defreeze'='Defreeze'; 'bank'='Bank'; 'kyc'='KYC'; 'gmail'='Gmail'; 'email'='Email'; 'cdr'='CDR'; 'transit'='Transit'; 'out station'='Outstation'; 'outstation'='Outstation'; 'bound down'='Bound Down'; 'challan'='Challan'; 'charge sheet'='Charge Sheet'; 'join'='Joining/Joining Investigation'
}

function ParseName([string]){
  =[regex]::Match(,'^CD\s*No\.?\s*(?<no>\d+)\s+dated\s+(?<date>\d{1,2}\.\d{1,2}\.\s*\d{2})(?:\s+(?<title>.*))?$', 'IgnoreCase')
  if(.Success){
    =[int].Groups['no'].Value
    =(.Groups['date'].Value -replace '\s','')
    =(.Groups['title'].Value).Trim()
    =
    [datetime]::TryParseExact(,'d.M.yy',,[System.Globalization.DateTimeStyles]::None,[ref]) | Out-Null
    return [pscustomobject]@{ CDNo=; Date=; Title= }
  } else {
    return [pscustomobject]@{ CDNo=; Date=; Title= }
  }
}

function DetectTags([string]){
  if([string]::IsNullOrWhiteSpace()){ return @() }
  =@()
  =.ToLowerInvariant()
  foreach( in .Keys){ if( -like ('*'++'*')){ +=[] } }
  return ( | Select-Object -Unique)
}

=@(); =@(); =@{}
Get-ChildItem -Filter '*.docx' | ForEach-Object {
  =[System.IO.Path]::GetFileNameWithoutExtension(.Name)
  =ParseName 
  =DetectTags .Title
  foreach( in ){ if(.ContainsKey()){ []++ } else { []=1 } }
   += [pscustomobject]@{ File=.Name; CDNo=.CDNo; Date= if(.Date){ .Date.ToString('yyyy-MM-dd') } else {  }; Title=.Title; Tags=( -join '; ') }
   += [pscustomobject]@{ CDNo=.CDNo; Date= if(.Date){ .Date.ToString('yyyy-MM-dd') } else {  }; Title=.Title; Tags= }
}

 | Sort-Object { if(.CDNo){ [int].CDNo } else { [int]::MaxValue } } | Export-Csv -NoTypeInformation -Path  -Encoding UTF8
 =  | Sort-Object @{Expression='Date';Descending=False}, @{Expression='CDNo';Descending=False} | ForEach-Object { [pscustomobject]@{ CDNo=.CDNo; Date=.Date; Title=.Title; Tags=(.Tags -join ', ') } }
=[pscustomobject]@{ Documents=.Count; TagCounts=(.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { [pscustomobject]@{ Tag=.Key; Count=.Value } }); Chronology= }
 | ConvertTo-Json -Depth 5 | Set-Content -Path  -Encoding UTF8

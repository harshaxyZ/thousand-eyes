# Attribution: WhatsMyName dataset

`wmn_data.json` in this directory is a snapshot (fetched 2026-11-03) of the
[WhatsMyName](https://github.com/WebBreacher/WhatsMyName) project dataset,
created by Micah "WebBreacher" Hoffmann and the OSINT community.

License: [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) -
this dataset is redistributed under the same license. Modification of the
dataset must remain CC-BY-SA; our checking *engine* code is separate and is
not derived from the WMN tool.

The thousand-eyes engine (`modules/username_checks.py`) reads the dataset's
check rules (uri_check, e_code, e_string, f_code, f_string) and performs its
own single-request interpretation per site. A stale/wrong marker results in
an "ambiguous" verdict that is silently dropped - never a false "found".

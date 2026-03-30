# Named pipes with JSON-RPC

Implement support for a daemon-like `jinjanator` that communicates using JSON-RPC over named pipes. The goal is repeated invocations without the cost of launching a new OS process for each. A subset of existing CLI parameters and functionality will be used and new CLI parameters will be added.

## Implementation details

- Follow the SRP and keep code DRY
- Use the `python-namedpipe` package (https://github.com/python-ffmpegio/python-namedpipe) to handle named pipes
- Use the `jsonrpyc` package (https://github.com/riga/jsonrpyc) to handle JSON-RPC
- The program should not detach from the controlling process and run in the background, rather it should wait until the process is terminated
- The user should be able to exit the program by pressing CTRL-C

### CLI parameters

- Add a string parameter `--named-pipe <pipe_name>` that indicates named pipe mode should be enabled and what the desired pipe name is. No other CLI parameters should be used.

### RPC interface

One RPC call named `jinjanate` accepts a JSON object as its argument. The JSON object will contain similar data to the existing CLI arguments and parameters; for example:

```json
{
  "template": "/tmp/foo/bar.j2",
  "data": "/tmp/foo/baz.yaml",
  "options": {
    "format": "YAML",
    "format-options": [],
    "import-env": "VAR",
    "output-file": "/tmp/foo/quux",
    "quiet": true,
    "undefined": false,
    "filters": [],
    "tests": [],
    "customize": ""
  }
}
```

- The existing `template` argument should not be treated any differently
- The existing `data` argument should be treated as a file path unless the argument begins with a hyphen, after which the remainder of the argument should be treated as the data used for rendering
- If the `output-file` option is specified the RPC output will contain the name of the output file and whether the write operation was successful. Otherwise, the RPC output will contain the rendered template.
- If an error occurs during rendering, the RPC output will contain the error message or messages

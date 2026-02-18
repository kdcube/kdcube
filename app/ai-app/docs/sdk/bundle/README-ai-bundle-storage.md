# AI Bundle Storage integration (localfs or S3)

## Configure the bundle storage
In .env file
```txt
CB_BUNDLE_STORAGE_URL=file:///absolute/path/prefix
```
> Soon will be exposed also in UI Admin Config.


## Somewhere inside your AIBundle __init__:
```python
self.storage = AIBundleStorage(
    tenant="acme",
    project="myproj",
    ai_bundle_id=self.id,              # pass the bundle's id here
    storage_uri=None,                  # or override e.g. "s3://bucket/prefix" / "file:///abs/path"
)
```

### Write text
```python
self.storage.write("logs/run1.txt", "hello world\n", mime="text/plain")
```

### Write bytes
```python
self.storage.write("artifacts/model.bin", model_bytes, mime="application/octet-stream")
```

### Read as text
```python
text = self.storage.read("logs/run1.txt", as_text=True)
```

### Read as bytes
```python
blob = self.storage.read("artifacts/model.bin")
```

### List immediate children under a prefix
```python
names = self.storage.list("logs/")
```

### Exists (file or prefix)
```python
self.storage.exists("logs/run1.txt")
self.storage.exists("artifacts/")     # prefix
```

### Delete a file
```python
self.storage.delete("logs/run1.txt")
```

### Delete a whole subtree under a prefix
```python
self.storage.delete("artifacts/")     # recursive
```

## Example in demo bundle
[default_app](../../default_app)

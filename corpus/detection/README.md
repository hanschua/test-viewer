# Detection-only fixtures

The detection manifest and detection golden in this directory verify grammar
detection. The root corpus manifest additionally references the unchanged capture
and `jdk25-g1.canonical.json` for partially verified normalization features.
Neither claim implies VERIFIED full runtime/collector parsing support.

`jdk25-g1.log` is captured locally from Temurin OpenJDK 25.0.3+9, HotSpot,
using the repository's `tools/CaptureGC.java` workload. The GC log does not
identify its distributor, so detected vendor remains unknown. Capture command:

```sh
java -Xms32m -Xmx32m -XX:+UseG1GC '-Xlog:gc*=info:file=corpus/detection/jdk25-g1.log' tools/CaptureGC.java
```

The generated log is contributed under CC0-1.0. It is retained verbatim; the
detection golden validates metadata and evidence only. The canonical golden
separately asserts young/full pauses, heap/metaspace values, CPU timing, phases,
source lines and parsing coverage.

The separate manifest is scoped `detection_only`. Unknown GC record counts are
null because production record recognition has not yet been measured. The root
parser capability manifest separately lists the foundation features as
PARTIALLY_VERIFIED. Concurrent cycles, mixed pauses, fallback/failure conditions
and other logging configurations remain outside that claim.

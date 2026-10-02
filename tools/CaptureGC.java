/** Self-generated detection fixture workload. No external libraries required. */
public class CaptureGC {
    private static volatile byte[] sink;

    public static void main(String[] args) {
        for (int i = 0; i < 400; i++) {
            sink = new byte[128 * 1024];
        }
        System.gc();
    }
}

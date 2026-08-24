import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;

/**
 * Does HttpURLConnection.setReadTimeout() actually take effect on the NEXT read() call if changed
 * mid-stream (after the connection is already open and some reads already happened)? The public
 * javadoc says it "must be called before the connection is established" but doesn't say changing
 * it later is illegal -- verify empirically instead of trusting memory.
 */
public class ReadTimeoutMidStreamSpike {
    public static void main(String[] args) throws Exception {
        String body = "{\"firstChunkDelayMs\":100,\"chunkIntervalMs\":1000,\"chunkCount\":10,"
                + "\"chunkSizeBytes\":16,\"stallAfterChunk\":2,\"stallMs\":30000}";
        URL url = new URL("http://localhost:8000/mock/stream");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("POST");
        connection.setRequestProperty("Content-Type", "application/json");
        connection.setConnectTimeout(3000);
        connection.setReadTimeout(120000); // long initial timeout
        connection.setDoOutput(true);
        byte[] bodyBytes = body.getBytes(StandardCharsets.UTF_8);
        connection.setFixedLengthStreamingMode(bodyBytes.length);
        OutputStream out = connection.getOutputStream();
        out.write(bodyBytes);
        out.flush();
        out.close();

        long start = System.nanoTime();
        BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
        String line;
        int lineNum = 0;
        while ((line = reader.readLine()) != null) {
            lineNum++;
            long elapsed = (System.nanoTime() - start) / 1_000_000;
            System.out.println("line " + lineNum + " at +" + elapsed + "ms: " + line);
            if (lineNum == 6) { // right after chunk2's blank line (3 lines per frame: event/data/blank x2 = 6)
                System.out.println(">>> shrinking read timeout to 800ms mid-stream at +" + elapsed + "ms");
                connection.setReadTimeout(800);
            }
        }
        System.out.println("reached EOF normally");
    }
}

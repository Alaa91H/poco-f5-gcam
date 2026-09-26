package dev.alaa.pocof5.camera2probe;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Size;
import android.view.Surface;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Configures one Pixel-Camera-like Camera2 output graph in an isolated process
 * lifetime. The runner force-stops the package between candidates so a Xiaomi
 * HAL failure cannot contaminate the next measurement.
 */
public final class PixelGraphProbeActivity extends Activity {
    private static final int CAMERA_PERMISSION_REQUEST = 1002;
    private static final long SESSION_TIMEOUT_MS = 8000;
    private static final String REPORT_NAME = "pixel-graph-runtime-report.json";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private TextView statusText;
    private boolean autoGenerateStarted;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(24 * getResources().getDisplayMetrics().density);
        root.setPadding(padding, padding, padding, padding);

        statusText = new TextView(this);
        statusText.setText("Ready to configure an isolated Pixel-style Camera2 graph.");
        statusText.setTextSize(16);
        root.addView(statusText, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);

    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!autoGenerateStarted
                && getIntent() != null
                && getIntent().getBooleanExtra("autoGenerate", false)) {
            autoGenerateStarted = true;
            ensurePermissionAndRun();
        }
    }

    private void ensurePermissionAndRun() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{Manifest.permission.CAMERA},
                    CAMERA_PERMISSION_REQUEST);
            return;
        }
        runProbe();
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != CAMERA_PERMISSION_REQUEST) {
            return;
        }
        if (grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            runProbe();
        } else {
            statusText.setText("Camera permission denied.");
        }
    }

    private void ensureCameraThread() {
        if (cameraThread != null) {
            return;
        }
        cameraThread = new HandlerThread("poco-f5-pixel-graph");
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());
    }

    private void runProbe() {
        ensureCameraThread();
        statusText.setText("Configuring requested Pixel-style stream graph...");

        executor.execute(() -> {
            try {
                String cameraId = getIntent().getStringExtra("cameraId");
                String candidate = getIntent().getStringExtra("candidate");
                if (cameraId == null || cameraId.isEmpty()) {
                    cameraId = "0";
                }
                if (candidate == null || candidate.isEmpty()) {
                    candidate = "private1280+raw10full+yuv1280";
                }

                JSONObject report = buildReport(this, cameraId, candidate);
                File output = new File(getFilesDir(), REPORT_NAME);
                try (OutputStreamWriter writer = new OutputStreamWriter(
                        new FileOutputStream(output, false),
                        StandardCharsets.UTF_8)) {
                    writer.write(report.toString(2));
                    writer.write("\n");
                }

                String finalCameraId = cameraId;
                String finalCandidate = candidate;
                runOnUiThread(() -> statusText.setText(
                        "Pixel graph report generated.\nCamera: " + finalCameraId +
                        "\nCandidate: " + finalCandidate));
            } catch (Exception e) {
                runOnUiThread(() -> statusText.setText("Pixel graph probe failed: " + e));
            }
        });
    }

    private JSONObject buildReport(
            Context context,
            String cameraId,
            String candidate) throws Exception {
        JSONObject root = new JSONObject();
        root.put("schemaVersion", 1);
        root.put("generatedAtUtc", utcNow());
        root.put("packageName", context.getPackageName());
        root.put("cameraId", cameraId);
        root.put("candidate", candidate);
        root.put("sessionTimeoutMs", SESSION_TIMEOUT_MS);

        CameraManager manager =
                (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
        JSONObject result = probeGraph(manager, cameraId, candidate);
        root.put("result", result);
        return root;
    }

    @SuppressLint({"MissingPermission", "NewApi"})
    private JSONObject probeGraph(
            CameraManager manager,
            String cameraId,
            String candidate) throws Exception {
        JSONObject result = new JSONObject();
        result.put("cameraId", cameraId);
        result.put("candidate", candidate);

        CameraCharacteristics characteristics =
                manager.getCameraCharacteristics(cameraId);
        StreamConfigurationMap map = characteristics.get(
                CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        if (map == null) {
            return result
                    .put("available", false)
                    .put("sessionConfigured", false)
                    .put("error", "No stream configuration map");
        }

        final boolean use1280;
        final boolean needRaw;
        final boolean needYuv;
        switch (candidate) {
            case "private800+raw10full":
                use1280 = false;
                needRaw = true;
                needYuv = false;
                break;
            case "private800+yuv800":
                use1280 = false;
                needRaw = false;
                needYuv = true;
                break;
            case "private800+raw10full+yuv800":
                use1280 = false;
                needRaw = true;
                needYuv = true;
                break;
            case "private1280+raw10full":
                use1280 = true;
                needRaw = true;
                needYuv = false;
                break;
            case "private1280+yuv1280":
                use1280 = true;
                needRaw = false;
                needYuv = true;
                break;
            case "private1280+raw10full+yuv1280":
                use1280 = true;
                needRaw = true;
                needYuv = true;
                break;
            default:
                return result
                        .put("available", false)
                        .put("sessionConfigured", false)
                        .put("error", "Unknown candidate: " + candidate);
        }

        int previewWidth = use1280 ? 1280 : 800;
        int previewHeight = use1280 ? 720 : 600;
        Size privateOutput = findExactSize(
                map.getOutputSizes(SurfaceTexture.class),
                previewWidth,
                previewHeight);
        Size raw10Full = findExactSize(
                map.getOutputSizes(ImageFormat.RAW10), 4624, 3472);
        Size yuvOutput = findExactSize(
                map.getOutputSizes(ImageFormat.YUV_420_888),
                previewWidth,
                previewHeight);

        JSONArray streams = new JSONArray();
        streams.put(streamJson("PRIVATE", privateOutput));
        if (needRaw) {
            streams.put(streamJson("RAW10", raw10Full));
        }
        if (needYuv) {
            streams.put(streamJson("YUV_420_888", yuvOutput));
        }
        result.put("streams", streams);

        boolean available = privateOutput != null
                && (!needRaw || raw10Full != null)
                && (!needYuv || yuvOutput != null);
        result.put("available", available);
        if (!available) {
            return result
                    .put("sessionConfigured", false)
                    .put("error", "One or more exact stream sizes are not advertised");
        }

        SurfaceTexture texture;
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            texture = new SurfaceTexture(false);
        } else {
            texture = new SurfaceTexture(0);
        }
        texture.setDefaultBufferSize(previewWidth, previewHeight);
        Surface privateSurface = new Surface(texture);
        ImageReader rawReader = needRaw
                ? ImageReader.newInstance(4624, 3472, ImageFormat.RAW10, 2)
                : null;
        ImageReader yuvReader = needYuv
                ? ImageReader.newInstance(
                        previewWidth,
                        previewHeight,
                        ImageFormat.YUV_420_888,
                        2)
                : null;

        List<Surface> surfaces = new ArrayList<>();
        surfaces.add(privateSurface);
        if (rawReader != null) {
            surfaces.add(rawReader.getSurface());
        }
        if (yuvReader != null) {
            surfaces.add(yuvReader.getSurface());
        }

        AtomicReference<CameraDevice> deviceRef = new AtomicReference<>();
        AtomicReference<CameraCaptureSession> sessionRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        long startedAtNs = System.nanoTime();

        try {
            manager.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    deviceRef.set(camera);
                    try {
                        camera.createCaptureSession(
                                surfaces,
                                new CameraCaptureSession.StateCallback() {
                                    @Override
                                    public void onConfigured(CameraCaptureSession session) {
                                        sessionRef.set(session);
                                        done.countDown();
                                    }

                                    @Override
                                    public void onConfigureFailed(
                                            CameraCaptureSession session) {
                                        sessionRef.set(session);
                                        errorRef.compareAndSet(
                                                null,
                                                "Capture session configuration failed");
                                        done.countDown();
                                    }
                                },
                                cameraHandler);
                    } catch (Exception e) {
                        errorRef.compareAndSet(
                                null,
                                "Create capture session: " + e);
                        done.countDown();
                    }
                }

                @Override
                public void onDisconnected(CameraDevice camera) {
                    deviceRef.compareAndSet(null, camera);
                    errorRef.compareAndSet(null, "Camera disconnected");
                    done.countDown();
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    deviceRef.compareAndSet(null, camera);
                    errorRef.compareAndSet(
                            null,
                            "CameraDevice error " + error);
                    done.countDown();
                }
            }, cameraHandler);

            boolean completed = done.await(SESSION_TIMEOUT_MS, TimeUnit.MILLISECONDS);
            long elapsedMs = TimeUnit.NANOSECONDS.toMillis(
                    System.nanoTime() - startedAtNs);
            String error = errorRef.get();
            boolean configured = completed && error == null && sessionRef.get() != null;

            result.put("completed", completed);
            result.put("sessionConfigured", configured);
            result.put("elapsedMs", elapsedMs);
            if (error != null) {
                result.put("error", error);
            } else if (!completed) {
                result.put("error", "Timed out waiting for capture-session configuration");
            }
        } finally {
            CameraCaptureSession session = sessionRef.get();
            if (session != null) {
                session.close();
            }
            CameraDevice device = deviceRef.get();
            if (device != null) {
                device.close();
            }
            if (rawReader != null) {
                rawReader.close();
            }
            if (yuvReader != null) {
                yuvReader.close();
            }
            privateSurface.release();
            texture.release();
        }

        return result;
    }

    private static JSONObject streamJson(String format, Size size) throws Exception {
        JSONObject stream = new JSONObject();
        stream.put("format", format);
        if (size == null) {
            stream.put("size", JSONObject.NULL);
        } else {
            stream.put("size", new JSONObject()
                    .put("width", size.getWidth())
                    .put("height", size.getHeight()));
        }
        return stream;
    }

    private static Size findExactSize(Size[] sizes, int width, int height) {
        if (sizes == null) {
            return null;
        }
        for (Size size : sizes) {
            if (size.getWidth() == width && size.getHeight() == height) {
                return size;
            }
        }
        return null;
    }

    private static String utcNow() {
        SimpleDateFormat format =
                new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        executor.shutdownNow();
        if (cameraThread != null) {
            cameraThread.quitSafely();
        }
    }
}

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
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Size;
import android.view.Surface;
import android.view.ViewGroup;
import android.widget.Button;
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
import java.util.Arrays;
import java.util.List;
import java.util.Date;
import java.util.Locale;
import java.util.Set;
import java.util.TimeZone;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

public final class YuvProbeActivity extends Activity {
    private static final int CAMERA_PERMISSION_REQUEST = 1001;
    private static final int TARGET_FRAMES = 12;
    private static final long CAMERA_TIMEOUT_MS = 7000;
    private static final String REPORT_NAME = "yuv-runtime-report.json";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private TextView statusText;
    private Button runButton;
    private HandlerThread cameraThread;
    private Handler cameraHandler;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(24 * getResources().getDisplayMetrics().density);
        root.setPadding(padding, padding, padding, padding);

        TextView title = new TextView(this);
        title.setText("POCO F5 YUV Runtime Probe");
        title.setTextSize(24);
        root.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView description = new TextView(this);
        description.setText(
                "Opens each exposed Camera2 ID and verifies sustained YUV_420_888 frames. " +
                "The test is read-only and stores only technical metadata.");
        description.setTextSize(16);
        LinearLayout.LayoutParams descriptionParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        descriptionParams.topMargin = padding / 2;
        root.addView(description, descriptionParams);

        runButton = new Button(this);
        runButton.setText("Run YUV runtime probe");
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        buttonParams.topMargin = padding;
        root.addView(runButton, buttonParams);

        statusText = new TextView(this);
        statusText.setText("Ready. Camera permission is required only for this active runtime test.");
        statusText.setTextIsSelectable(true);
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        statusParams.topMargin = padding;
        root.addView(statusText, statusParams);

        setContentView(root);

        runButton.setOnClickListener(v -> ensurePermissionAndRun());

        if (getIntent() != null && getIntent().getBooleanExtra("autoGenerate", false)) {
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
            statusText.setText("Camera permission was denied. Runtime YUV probing was not performed.");
        }
    }

    private void ensureCameraThread() {
        if (cameraThread != null) {
            return;
        }
        cameraThread = new HandlerThread("poco-f5-yuv-probe");
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());
    }

    private void runProbe() {
        runButton.setEnabled(false);
        statusText.setText("Testing YUV streams across exposed camera IDs...");
        ensureCameraThread();

        executor.execute(() -> {
            try {
                JSONObject report = buildRuntimeReport(this);
                File output = new File(getFilesDir(), REPORT_NAME);
                try (OutputStreamWriter writer = new OutputStreamWriter(
                        new FileOutputStream(output, false),
                        StandardCharsets.UTF_8)) {
                    writer.write(report.toString(2));
                    writer.write("\n");
                }

                runOnUiThread(() -> {
                    statusText.setText(
                            "YUV runtime report generated successfully.\n\n" +
                            "Internal path:\n" + output.getAbsolutePath() + "\n\n" +
                            "Use scripts/run-camera2-probe.ps1 -YuvRuntime to export it.");
                    runButton.setEnabled(true);
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    statusText.setText("YUV runtime probe failed:\n" + e);
                    runButton.setEnabled(true);
                });
            }
        });
    }

    private JSONObject buildRuntimeReport(Context context) throws Exception {
        JSONObject root = new JSONObject();
        root.put("schemaVersion", 2);
        root.put("generatedAtUtc", utcNow());
        root.put("packageName", context.getPackageName());
        root.put("targetFramesPerCamera", TARGET_FRAMES);
        root.put("timeoutMsPerCamera", CAMERA_TIMEOUT_MS);

        CameraManager manager =
                (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
        String[] ids = manager.getCameraIdList();
        Arrays.sort(ids);

        JSONArray cameras = new JSONArray();
        for (String id : ids) {
            cameras.put(probeCamera(manager, id, ids));
        }
        root.put("cameras", cameras);
        return root;
    }

    @SuppressLint("MissingPermission")
    private JSONObject probeCamera(
            CameraManager manager,
            String cameraId,
            String[] exposedIds) throws Exception {
        JSONObject result = new JSONObject();
        result.put("id", cameraId);

        CameraCharacteristics characteristics =
                manager.getCameraCharacteristics(cameraId);
        JSONArray physicalArray = new JSONArray();
        if (android.os.Build.VERSION.SDK_INT >= 28) {
            Set<String> physicalIds = characteristics.getPhysicalCameraIds();
            for (String physicalId : physicalIds) {
                physicalArray.put(new JSONObject()
                        .put("id", physicalId)
                        .put("directlyExposed", Arrays.asList(exposedIds).contains(physicalId)));
            }
        }
        result.put("physicalCameraIds", physicalArray);

        StreamConfigurationMap map = characteristics.get(
                CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        if (map == null) {
            return result
                    .put("advertisedYuv", false)
                    .put("success", false)
                    .put("reason", "No stream configuration map");
        }

        Size[] yuvSizes = map.getOutputSizes(ImageFormat.YUV_420_888);
        if (yuvSizes == null || yuvSizes.length == 0) {
            return result
                    .put("advertisedYuv", false)
                    .put("success", false)
                    .put("reason", "YUV_420_888 is not advertised");
        }

        result.put("advertisedYuv", true);
        Size selected = chooseProbeSize(yuvSizes);
        result.put("selectedSize", new JSONObject()
                .put("width", selected.getWidth())
                .put("height", selected.getHeight()));

        ImageReader reader = ImageReader.newInstance(
                selected.getWidth(),
                selected.getHeight(),
                ImageFormat.YUV_420_888,
                4);

        AtomicReference<CameraDevice> deviceRef = new AtomicReference<>();
        AtomicReference<CameraCaptureSession> sessionRef = new AtomicReference<>();
        AtomicReference<JSONArray> sessionMatrixRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();
        AtomicInteger frameCount = new AtomicInteger();
        AtomicInteger planeCount = new AtomicInteger(-1);
        AtomicInteger yRowStride = new AtomicInteger(-1);
        AtomicInteger yPixelStride = new AtomicInteger(-1);
        AtomicLong firstImageTimestampNs = new AtomicLong(-1);
        AtomicLong lastImageTimestampNs = new AtomicLong(-1);
        CountDownLatch done = new CountDownLatch(1);
        long startedAtNs = System.nanoTime();

        reader.setOnImageAvailableListener(source -> {
            Image image = null;
            try {
                image = source.acquireLatestImage();
                if (image == null) {
                    return;
                }

                int current = frameCount.incrementAndGet();
                if (current == 1) {
                    firstImageTimestampNs.set(image.getTimestamp());
                    Image.Plane[] planes = image.getPlanes();
                    planeCount.set(planes.length);
                    if (planes.length > 0) {
                        yRowStride.set(planes[0].getRowStride());
                        yPixelStride.set(planes[0].getPixelStride());
                    }
                }
                lastImageTimestampNs.set(image.getTimestamp());

                if (current >= TARGET_FRAMES) {
                    done.countDown();
                }
            } catch (RuntimeException e) {
                errorRef.compareAndSet(null, "Image callback: " + e);
                done.countDown();
            } finally {
                if (image != null) {
                    image.close();
                }
            }
        }, cameraHandler);

        try {
            manager.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    deviceRef.set(camera);
                    try {
                        sessionMatrixRef.set(buildSessionSupportMatrix(camera, map, reader));
                    } catch (Exception e) {
                        JSONArray matrixError = new JSONArray();
                        try {
                            matrixError.put(new JSONObject()
                                    .put("name", "matrix_probe_error")
                                    .put("error", e.toString()));
                        } catch (Exception ignored) {
                            matrixError.put("matrix_probe_error: " + e);
                        }
                        sessionMatrixRef.set(matrixError);
                    }
                    try {
                        camera.createCaptureSession(
                                Arrays.asList(reader.getSurface()),
                                new CameraCaptureSession.StateCallback() {
                                    @Override
                                    public void onConfigured(CameraCaptureSession session) {
                                        sessionRef.set(session);
                                        try {
                                            CaptureRequest.Builder request =
                                                    camera.createCaptureRequest(
                                                            CameraDevice.TEMPLATE_PREVIEW);
                                            request.addTarget(reader.getSurface());
                                            request.set(
                                                    CaptureRequest.CONTROL_MODE,
                                                    CaptureRequest.CONTROL_MODE_AUTO);
                                            session.setRepeatingRequest(
                                                    request.build(),
                                                    null,
                                                    cameraHandler);
                                        } catch (Exception e) {
                                            errorRef.compareAndSet(
                                                    null,
                                                    "Start repeating YUV request: " + e);
                                            done.countDown();
                                        }
                                    }

                                    @Override
                                    public void onConfigureFailed(
                                            CameraCaptureSession session) {
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
                    errorRef.compareAndSet(null, "Camera disconnected");
                    done.countDown();
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    errorRef.compareAndSet(null, "CameraDevice error " + error);
                    done.countDown();
                }
            }, cameraHandler);

            boolean completed = done.await(CAMERA_TIMEOUT_MS, TimeUnit.MILLISECONDS);
            long elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNs);
            int frames = frameCount.get();
            boolean success = completed && frames >= TARGET_FRAMES && errorRef.get() == null;

            result.put("success", success);
            JSONArray matrix = sessionMatrixRef.get();
            result.put("sessionSupportMatrix",
                    matrix == null ? new JSONArray() : matrix);
            result.put("framesReceived", frames);
            result.put("elapsedMs", elapsedMs);
            result.put("planeCount", planeCount.get());
            result.put("yPlaneRowStride", yRowStride.get());
            result.put("yPlanePixelStride", yPixelStride.get());
            result.put(
                    "firstImageTimestampNs",
                    firstImageTimestampNs.get() < 0
                            ? JSONObject.NULL
                            : firstImageTimestampNs.get());
            result.put(
                    "lastImageTimestampNs",
                    lastImageTimestampNs.get() < 0
                            ? JSONObject.NULL
                            : lastImageTimestampNs.get());

            String error = errorRef.get();
            if (error != null) {
                result.put("error", error);
            } else if (!completed) {
                result.put("error", "Timed out waiting for sustained YUV frames");
            }
        } finally {
            CameraCaptureSession session = sessionRef.get();
            if (session != null) {
                try {
                    session.stopRepeating();
                } catch (Exception ignored) {
                }
                session.close();
            }
            CameraDevice device = deviceRef.get();
            if (device != null) {
                device.close();
            }
            reader.close();
        }

        return result;
    }

    private JSONArray buildSessionSupportMatrix(
            CameraDevice camera,
            StreamConfigurationMap map,
            ImageReader yuvReader) throws Exception {
        JSONArray matrix = new JSONArray();

        Size[] previewSizes = map.getOutputSizes(SurfaceTexture.class);
        Size[] jpegSizes = map.getOutputSizes(ImageFormat.JPEG);
        Size[] yuvSizes = map.getOutputSizes(ImageFormat.YUV_420_888);

        Size previewSize = chooseOptionalProbeSize(previewSizes);
        Size jpegSize = chooseOptionalProbeSize(jpegSizes);
        Size yuvSize = chooseOptionalProbeSize(yuvSizes);

        SurfaceTexture previewTexture = null;
        Surface previewSurface = null;
        ImageReader jpegReader = null;
        try {
            if (previewSize != null) {
                previewTexture = new SurfaceTexture(0);
                previewTexture.setDefaultBufferSize(
                        previewSize.getWidth(),
                        previewSize.getHeight());
                previewSurface = new Surface(previewTexture);
            }
            if (jpegSize != null) {
                jpegReader = ImageReader.newInstance(
                        jpegSize.getWidth(),
                        jpegSize.getHeight(),
                        ImageFormat.JPEG,
                        2);
            }

            Executor sessionExecutor = command -> cameraHandler.post(command);
            CameraCaptureSession.StateCallback callback =
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession session) {}

                        @Override
                        public void onConfigureFailed(CameraCaptureSession session) {}
                    };

            addSessionCandidate(
                    matrix,
                    camera,
                    sessionExecutor,
                    callback,
                    "preview",
                    previewSize,
                    previewSurface);
            addSessionCandidate(
                    matrix,
                    camera,
                    sessionExecutor,
                    callback,
                    "yuv",
                    yuvSize,
                    yuvReader.getSurface());
            addSessionCandidate(
                    matrix,
                    camera,
                    sessionExecutor,
                    callback,
                    "jpeg",
                    jpegSize,
                    jpegReader == null ? null : jpegReader.getSurface());

            addSessionCandidate(
                    matrix,
                    camera,
                    sessionExecutor,
                    callback,
                    "preview+yuv",
                    new Size[]{previewSize, yuvSize},
                    new Surface[]{previewSurface, yuvReader.getSurface()});
            addSessionCandidate(
                    matrix,
                    camera,
                    sessionExecutor,
                    callback,
                    "preview+jpeg",
                    new Size[]{previewSize, jpegSize},
                    new Surface[]{
                            previewSurface,
                            jpegReader == null ? null : jpegReader.getSurface()
                    });
            addSessionCandidate(
                    matrix,
                    camera,
                    sessionExecutor,
                    callback,
                    "yuv+jpeg",
                    new Size[]{yuvSize, jpegSize},
                    new Surface[]{
                            yuvReader.getSurface(),
                            jpegReader == null ? null : jpegReader.getSurface()
                    });
            addSessionCandidate(
                    matrix,
                    camera,
                    sessionExecutor,
                    callback,
                    "preview+yuv+jpeg",
                    new Size[]{previewSize, yuvSize, jpegSize},
                    new Surface[]{
                            previewSurface,
                            yuvReader.getSurface(),
                            jpegReader == null ? null : jpegReader.getSurface()
                    });
        } finally {
            if (jpegReader != null) {
                jpegReader.close();
            }
            if (previewSurface != null) {
                previewSurface.release();
            }
            if (previewTexture != null) {
                previewTexture.release();
            }
        }

        return matrix;
    }

    private static void addSessionCandidate(
            JSONArray matrix,
            CameraDevice camera,
            Executor executor,
            CameraCaptureSession.StateCallback callback,
            String name,
            Size size,
            Surface surface) throws Exception {
        addSessionCandidate(
                matrix,
                camera,
                executor,
                callback,
                name,
                new Size[]{size},
                new Surface[]{surface});
    }

    private static void addSessionCandidate(
            JSONArray matrix,
            CameraDevice camera,
            Executor executor,
            CameraCaptureSession.StateCallback callback,
            String name,
            Size[] sizes,
            Surface[] surfaces) throws Exception {
        JSONObject item = new JSONObject();
        item.put("name", name);

        JSONArray sizeArray = new JSONArray();
        boolean available = true;
        for (Size size : sizes) {
            if (size == null) {
                available = false;
                sizeArray.put(JSONObject.NULL);
            } else {
                sizeArray.put(new JSONObject()
                        .put("width", size.getWidth())
                        .put("height", size.getHeight()));
            }
        }
        item.put("sizes", sizeArray);
        item.put("available", available);

        if (!available) {
            item.put("supported", JSONObject.NULL);
            matrix.put(item);
            return;
        }

        List<OutputConfiguration> outputs = new ArrayList<>();
        for (Surface surface : surfaces) {
            if (surface == null) {
                available = false;
                break;
            }
            outputs.add(new OutputConfiguration(surface));
        }
        if (!available) {
            item.put("supported", JSONObject.NULL);
            matrix.put(item);
            return;
        }

        if (android.os.Build.VERSION.SDK_INT >= 28) {
            try {
                SessionConfiguration config = new SessionConfiguration(
                        SessionConfiguration.SESSION_REGULAR,
                        outputs,
                        executor,
                        callback);
                item.put(
                        "supported",
                        camera.isSessionConfigurationSupported(config));
            } catch (RuntimeException e) {
                item.put("supported", JSONObject.NULL);
                item.put("error", e.toString());
            }
        } else {
            item.put("supported", JSONObject.NULL);
        }

        matrix.put(item);
    }

    private static Size chooseOptionalProbeSize(Size[] sizes) {
        if (sizes == null || sizes.length == 0) {
            return null;
        }
        return chooseProbeSize(sizes);
    }

    private static Size chooseProbeSize(Size[] sizes) {
        Size[] copy = sizes.clone();
        Arrays.sort(copy, (a, b) -> Long.compare(
                (long) b.getWidth() * b.getHeight(),
                (long) a.getWidth() * a.getHeight()));

        long targetArea = 1920L * 1080L;
        for (Size size : copy) {
            long area = (long) size.getWidth() * size.getHeight();
            if (area <= targetArea) {
                return size;
            }
        }
        return copy[copy.length - 1];
    }

    private static String utcNow() {
        SimpleDateFormat format =
                new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        if (cameraThread != null) {
            cameraThread.quitSafely();
        }
        super.onDestroy();
    }
}

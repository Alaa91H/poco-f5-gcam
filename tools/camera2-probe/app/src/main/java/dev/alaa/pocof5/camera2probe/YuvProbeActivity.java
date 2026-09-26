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
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

public final class YuvProbeActivity extends Activity {
    private static final int CAMERA_PERMISSION_REQUEST = 1001;
    private static final int TARGET_FRAMES = 12;
    private static final long CAMERA_TIMEOUT_MS = 7000;
    private static final long CAMERA_AVAILABILITY_TIMEOUT_MS = 2500;
    private static final long SESSION_QUERY_TIMEOUT_MS = 1500;
    // 0.3.9 also queries the exact Pixel Camera stream graph observed on marble.
    private static final String REPORT_NAME = "yuv-runtime-report.json";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private TextView statusText;
    private Button runButton;
    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private boolean autoGenerateStarted;

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
                String requestedCameraId = null;
                if (getIntent() != null) {
                    requestedCameraId = getIntent().getStringExtra("cameraId");
                }
                JSONObject report = buildRuntimeReport(this, requestedCameraId);
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

    private JSONObject buildRuntimeReport(
            Context context,
            String requestedCameraId) throws Exception {
        JSONObject root = new JSONObject();
        root.put("schemaVersion", 2);
        root.put("generatedAtUtc", utcNow());
        root.put("packageName", context.getPackageName());
        root.put("targetFramesPerCamera", TARGET_FRAMES);
        root.put("timeoutMsPerCamera", CAMERA_TIMEOUT_MS);
        root.put("availabilityTimeoutMsPerCamera", CAMERA_AVAILABILITY_TIMEOUT_MS);
        root.put("sessionQueryTimeoutMs", SESSION_QUERY_TIMEOUT_MS);

        CameraManager manager =
                (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
        String[] ids = manager.getCameraIdList();
        Arrays.sort(ids);

        if (requestedCameraId != null && !requestedCameraId.isEmpty()) {
            root.put("requestedCameraId", requestedCameraId);
        }

        JSONArray cameras = new JSONArray();
        for (String id : ids) {
            if (requestedCameraId != null
                    && !requestedCameraId.isEmpty()
                    && !requestedCameraId.equals(id)) {
                continue;
            }
            cameras.put(probeCamera(manager, id, ids));
        }
        if (requestedCameraId != null
                && !requestedCameraId.isEmpty()
                && cameras.length() == 0) {
            throw new IllegalArgumentException(
                    "Requested camera ID is not exposed: " + requestedCameraId);
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

        JSONArray sessionMatrix = buildSessionSupportMatrix(
                manager,
                cameraId,
                map);
        result.put("sessionSupportMatrix", sessionMatrix);

        ImageReader reader = ImageReader.newInstance(
                selected.getWidth(),
                selected.getHeight(),
                ImageFormat.YUV_420_888,
                4);

        AtomicReference<CameraDevice> deviceRef = new AtomicReference<>();
        AtomicReference<CameraCaptureSession> sessionRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();
        AtomicInteger frameCount = new AtomicInteger();
        AtomicInteger planeCount = new AtomicInteger(-1);
        AtomicInteger yRowStride = new AtomicInteger(-1);
        AtomicInteger yPixelStride = new AtomicInteger(-1);
        AtomicLong firstImageTimestampNs = new AtomicLong(-1);
        AtomicLong lastImageTimestampNs = new AtomicLong(-1);
        CountDownLatch done = new CountDownLatch(1);
        long startedAtNs = System.nanoTime();

        CountDownLatch availableLatch = new CountDownLatch(1);
        AtomicInteger unavailableEventsBeforeOpen = new AtomicInteger();
        CameraManager.AvailabilityCallback availabilityCallback =
                new CameraManager.AvailabilityCallback() {
                    @Override
                    public void onCameraAvailable(String id) {
                        if (cameraId.equals(id)) {
                            availableLatch.countDown();
                        }
                    }

                    @Override
                    public void onCameraUnavailable(String id) {
                        if (cameraId.equals(id) && availableLatch.getCount() > 0) {
                            unavailableEventsBeforeOpen.incrementAndGet();
                        }
                    }
                };

        long availabilityStartedAtNs = System.nanoTime();
        manager.registerAvailabilityCallback(availabilityCallback, cameraHandler);
        boolean availableBeforeOpen = availableLatch.await(
                CAMERA_AVAILABILITY_TIMEOUT_MS,
                TimeUnit.MILLISECONDS);
        long availabilityWaitMs = TimeUnit.NANOSECONDS.toMillis(
                System.nanoTime() - availabilityStartedAtNs);
        result.put("availableBeforeOpen", availableBeforeOpen);
        result.put("availabilityWaitMs", availabilityWaitMs);
        result.put(
                "unavailableEventsBeforeOpen",
                unavailableEventsBeforeOpen.get());

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
                    deviceRef.compareAndSet(null, camera);
                    errorRef.compareAndSet(null, "Camera disconnected");
                    done.countDown();
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    deviceRef.compareAndSet(null, camera);
                    errorRef.compareAndSet(
                            null,
                            "CameraDevice error " + error + " (" + cameraErrorName(error) + ")");
                    done.countDown();
                }
            }, cameraHandler);

            boolean completed = done.await(CAMERA_TIMEOUT_MS, TimeUnit.MILLISECONDS);
            long elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAtNs);
            int frames = frameCount.get();
            boolean success = completed && frames >= TARGET_FRAMES && errorRef.get() == null;

            result.put("success", success);
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
            manager.unregisterAvailabilityCallback(availabilityCallback);
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

    @SuppressLint("NewApi")
    @android.annotation.TargetApi(35)
    private JSONArray buildSessionSupportMatrix(
            CameraManager manager,
            String cameraId,
            StreamConfigurationMap map) throws Exception {
        JSONArray matrix = new JSONArray();

        Size previewSize = chooseOptionalProbeSize(
                map.getOutputSizes(SurfaceTexture.class));
        Size jpegSize = chooseOptionalProbeSize(
                map.getOutputSizes(ImageFormat.JPEG));
        Size yuvSize = chooseOptionalProbeSize(
                map.getOutputSizes(ImageFormat.YUV_420_888));

        // Pixel Camera runtime evidence on marble shows this exact standard
        // Camera2 graph immediately before Xiaomi's HAL rejects configuration:
        // PRIVATE 800x600 + RAW10 4624x3472 + YUV_420_888 800x600.
        // Query it explicitly so we can distinguish an unsupported stream
        // combination from Pixel-specific session/vendor parameters.
        Size pixelPrivate800 = findExactSize(
                map.getOutputSizes(SurfaceTexture.class), 800, 600);
        Size pixelRaw10Full = findExactSize(
                map.getOutputSizes(ImageFormat.RAW10), 4624, 3472);
        Size pixelYuv800 = findExactSize(
                map.getOutputSizes(ImageFormat.YUV_420_888), 800, 600);

        CameraDevice.CameraDeviceSetup setup = null;
        String setupError = null;
        if (android.os.Build.VERSION.SDK_INT >= 35) {
            try {
                if (manager.isCameraDeviceSetupSupported(cameraId)) {
                    setup = manager.getCameraDeviceSetup(cameraId);
                } else {
                    setupError = "CameraDeviceSetup is not supported";
                }
            } catch (Exception e) {
                setupError = e.toString();
            }
        } else {
            setupError = "CameraDeviceSetup requires API 35+";
        }

        Executor directExecutor = Runnable::run;
        CameraCaptureSession.StateCallback callback =
                new CameraCaptureSession.StateCallback() {
                    @Override
                    public void onConfigured(CameraCaptureSession session) {}

                    @Override
                    public void onConfigureFailed(CameraCaptureSession session) {}
                };

        boolean queryEnabled = setup != null;
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "preview",
                new Size[]{previewSize},
                new int[]{-1});
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "yuv",
                new Size[]{yuvSize},
                new int[]{ImageFormat.YUV_420_888});
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "jpeg",
                new Size[]{jpegSize},
                new int[]{ImageFormat.JPEG});
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "preview+yuv",
                new Size[]{previewSize, yuvSize},
                new int[]{-1, ImageFormat.YUV_420_888});
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "preview+jpeg",
                new Size[]{previewSize, jpegSize},
                new int[]{-1, ImageFormat.JPEG});
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "yuv+jpeg",
                new Size[]{yuvSize, jpegSize},
                new int[]{ImageFormat.YUV_420_888, ImageFormat.JPEG});
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "preview+yuv+jpeg",
                new Size[]{previewSize, yuvSize, jpegSize},
                new int[]{-1, ImageFormat.YUV_420_888, ImageFormat.JPEG});

        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "pixel-private800+raw10full",
                new Size[]{pixelPrivate800, pixelRaw10Full},
                new int[]{-1, ImageFormat.RAW10});
        queryEnabled = addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "pixel-private800+yuv800",
                new Size[]{pixelPrivate800, pixelYuv800},
                new int[]{-1, ImageFormat.YUV_420_888});
        addSetupSessionCandidate(
                matrix,
                setup,
                cameraId,
                directExecutor,
                callback,
                setupError,
                queryEnabled,
                "pixel-private800+raw10full+yuv800",
                new Size[]{pixelPrivate800, pixelRaw10Full, pixelYuv800},
                new int[]{-1, ImageFormat.RAW10, ImageFormat.YUV_420_888});

        return matrix;
    }

    @SuppressLint("NewApi")
    @android.annotation.TargetApi(35)
    private static boolean addSetupSessionCandidate(
            JSONArray matrix,
            CameraDevice.CameraDeviceSetup setup,
            String cameraId,
            Executor executor,
            CameraCaptureSession.StateCallback callback,
            String setupError,
            boolean queryEnabled,
            String name,
            Size[] sizes,
            int[] formats) throws Exception {
        JSONObject item = new JSONObject();
        item.put("name", name);
        item.put("queryMode", "CameraDeviceSetup");

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
            return queryEnabled;
        }

        if (setup == null) {
            item.put("supported", JSONObject.NULL);
            item.put("error", setupError == null
                    ? "CameraDeviceSetup unavailable"
                    : setupError);
            matrix.put(item);
            return false;
        }

        if (!queryEnabled) {
            item.put("supported", JSONObject.NULL);
            item.put("error", "Skipped after prior session-query timeout");
            matrix.put(item);
            return false;
        }

        List<OutputConfiguration> outputs = new ArrayList<>();
        for (int i = 0; i < sizes.length; i++) {
            if (formats[i] == -1) {
                outputs.add(new OutputConfiguration(
                        sizes[i],
                        SurfaceTexture.class));
            } else {
                outputs.add(new OutputConfiguration(
                        formats[i],
                        sizes[i]));
            }
        }

        SessionConfiguration config = new SessionConfiguration(
                SessionConfiguration.SESSION_REGULAR,
                outputs,
                executor,
                callback);

        FutureTask<Boolean> query = new FutureTask<>(
                () -> setup.isSessionConfigurationSupported(config));
        Thread queryThread = new Thread(
                query,
                "camera-session-query-" + cameraId + "-" + name);
        queryThread.setDaemon(true);
        queryThread.start();

        try {
            item.put(
                    "supported",
                    query.get(SESSION_QUERY_TIMEOUT_MS, TimeUnit.MILLISECONDS));
        } catch (TimeoutException e) {
            query.cancel(true);
            item.put("supported", JSONObject.NULL);
            item.put(
                    "error",
                    "Timed out after " + SESSION_QUERY_TIMEOUT_MS + " ms");
            item.put("queryTimedOut", true);
            matrix.put(item);
            return false;
        } catch (Exception e) {
            item.put("supported", JSONObject.NULL);
            item.put("error", e.toString());
        }

        matrix.put(item);
        return true;
    }

    private static Size findExactSize(
            Size[] sizes,
            int width,
            int height) {
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

    private static String cameraErrorName(int error) {
        switch (error) {
            case CameraDevice.StateCallback.ERROR_CAMERA_IN_USE:
                return "ERROR_CAMERA_IN_USE";
            case CameraDevice.StateCallback.ERROR_MAX_CAMERAS_IN_USE:
                return "ERROR_MAX_CAMERAS_IN_USE";
            case CameraDevice.StateCallback.ERROR_CAMERA_DISABLED:
                return "ERROR_CAMERA_DISABLED";
            case CameraDevice.StateCallback.ERROR_CAMERA_DEVICE:
                return "ERROR_CAMERA_DEVICE";
            case CameraDevice.StateCallback.ERROR_CAMERA_SERVICE:
                return "ERROR_CAMERA_SERVICE";
            default:
                return "UNKNOWN_CAMERA_ERROR";
        }
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

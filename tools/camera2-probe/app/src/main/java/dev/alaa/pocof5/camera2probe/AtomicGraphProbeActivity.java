package dev.alaa.pocof5.camera2probe;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CameraMetadata;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.InputConfiguration;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Range;
import android.util.Size;
import android.view.Surface;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Runs one fully parameterized Camera2 graph in an isolated app lifetime. */
public final class AtomicGraphProbeActivity extends Activity {
    private static final int CAMERA_PERMISSION_REQUEST = 1003;
    private static final long TIMEOUT_MS = 8000;
    private static final String REPORT_NAME = "atomic-graph-runtime-report.json";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private TextView statusText;
    private boolean started;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        statusText = new TextView(this);
        statusText.setPadding(48, 48, 48, 48);
        statusText.setText("Atomic graph probe ready.");
        setContentView(statusText);
    }
    @Override
    protected void onResume() {
        super.onResume();
        if (!started && getIntent().getBooleanExtra("autoGenerate", false)) {
            started = true;
            ensurePermissionAndRun();
        }
    }

    private void ensurePermissionAndRun() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION_REQUEST);
            return;
        }
        runProbe();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_PERMISSION_REQUEST
                && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            runProbe();
        }
    }

    private void ensureCameraThread() {
        if (cameraThread != null) return;
        cameraThread = new HandlerThread("poco-f5-atomic-graph");
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());
    }
    private void runProbe() {
        ensureCameraThread();
        executor.execute(() -> {
            try {
                JSONObject report = probe();
                File output = new File(getFilesDir(), REPORT_NAME);
                try (OutputStreamWriter writer = new OutputStreamWriter(
                        new FileOutputStream(output, false), StandardCharsets.UTF_8)) {
                    writer.write(report.toString(2));
                    writer.write("\n");
                }
                runOnUiThread(() -> {
                    statusText.setText("Atomic graph report generated.");
                    finishAndRemoveTask();
                });
            } catch (Exception e) {
                runOnUiThread(() -> statusText.setText("Atomic graph probe failed: " + e));
            }
        });
    }

    @SuppressLint({"MissingPermission", "NewApi"})
    private JSONObject probe() throws Exception {
        String cameraId = getIntent().getStringExtra("cameraId");
        if (cameraId == null) cameraId = "0";
        String spec = getIntent().getStringExtra("streams");
        if (spec == null || spec.isEmpty()) spec = "private,raw10,yuv";
        List<String> streamNames = Arrays.asList(spec.split(","));

        int previewWidth = getIntent().getIntExtra("previewWidth", 1280);
        int previewHeight = getIntent().getIntExtra("previewHeight", 720);
        int privateMax = getIntent().getIntExtra("privateMax", 11);
        int rawMax = getIntent().getIntExtra("rawMax", 30);
        int yuvMax = getIntent().getIntExtra("yuvMax", 52);
        long privateUsage = getIntent().getLongExtra("privateUsage", -1L);
        long rawUsage = getIntent().getLongExtra("rawUsage", -1L);
        long yuvUsage = getIntent().getLongExtra("yuvUsage", -1L);
        boolean sessionCfg = getIntent().getBooleanExtra("sessionCfg", true);
        int sessionType = getIntent().getIntExtra(
                "sessionType", SessionConfiguration.SESSION_REGULAR);
        boolean pixelMode0 = getIntent().getBooleanExtra("pixelMode0", false);
        String clientName = getIntent().getStringExtra("clientName");
        int videoStab = getIntent().getIntExtra("videoStab", -1);
        int sceneMode = getIntent().getIntExtra("sceneMode", -1);
        int fpsLower = getIntent().getIntExtra("fpsLower", -1);
        int fpsUpper = getIntent().getIntExtra("fpsUpper", -1);
        String inputFormatName = getIntent().getStringExtra("inputFormat");
        int inputWidth = getIntent().getIntExtra("inputWidth", 4624);
        int inputHeight = getIntent().getIntExtra("inputHeight", 3472);

        CameraManager manager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
        CameraCharacteristics characteristics = manager.getCameraCharacteristics(cameraId);
        StreamConfigurationMap map =
                characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        if (map == null) throw new IllegalStateException("No stream configuration map");

        Size privateSize = findExact(map.getOutputSizes(ImageFormat.PRIVATE),
                previewWidth, previewHeight);
        Size yuvSize = findExact(map.getOutputSizes(ImageFormat.YUV_420_888),
                previewWidth, previewHeight);
        Size rawSize = largest(map.getOutputSizes(ImageFormat.RAW10));

        JSONObject root = new JSONObject();
        root.put("cameraId", cameraId);
        root.put("streamsRequested", new JSONArray(streamNames));
        root.put("previewSize", previewWidth + "x" + previewHeight);
        root.put("rawSize", rawSize == null ? JSONObject.NULL
                : rawSize.getWidth() + "x" + rawSize.getHeight());
        root.put("sessionCfg", sessionCfg);
        root.put("sessionType", sessionType);
        root.put("pixelMode0", pixelMode0);
        root.put("clientName", clientName == null ? JSONObject.NULL : clientName);
        root.put("videoStab", videoStab);
        root.put("sceneMode", sceneMode);
        root.put("fps", fpsLower + "-" + fpsUpper);
        root.put("privateUsage", privateUsage);
        root.put("rawUsage", rawUsage);
        root.put("yuvUsage", yuvUsage);
        root.put("inputFormat", inputFormatName == null ? JSONObject.NULL : inputFormatName);
        root.put("inputSize", inputWidth + "x" + inputHeight);
        List<ImageReader> readers = new ArrayList<>();
        List<Surface> surfaces = new ArrayList<>();
        JSONArray actualStreams = new JSONArray();

        for (String streamName : streamNames) {
            String name = streamName.trim().toLowerCase();
            ImageReader reader;
            if ("private".equals(name)) {
                requireSize("PRIVATE", privateSize);
                reader = buildReader(
                        privateSize,
                        ImageFormat.PRIVATE, privateMax, privateUsage);
                actualStreams.put(streamJson("PRIVATE", privateSize, privateMax));
                actualStreams.getJSONObject(actualStreams.length() - 1).put("usage", reader.getUsage());
                actualStreams.getJSONObject(actualStreams.length() - 1).put("hardwareBufferFormat", reader.getHardwareBufferFormat());
                actualStreams.getJSONObject(actualStreams.length() - 1).put("dataSpace", reader.getDataSpace());
            } else if ("raw10".equals(name)) {
                requireSize("RAW10", rawSize);
                reader = buildReader(
                        rawSize,
                        ImageFormat.RAW10, rawMax, rawUsage);
                actualStreams.put(streamJson("RAW10", rawSize, rawMax));
                actualStreams.getJSONObject(actualStreams.length() - 1).put("usage", reader.getUsage());
                actualStreams.getJSONObject(actualStreams.length() - 1).put("hardwareBufferFormat", reader.getHardwareBufferFormat());
                actualStreams.getJSONObject(actualStreams.length() - 1).put("dataSpace", reader.getDataSpace());
            } else if ("yuv".equals(name)) {
                requireSize("YUV", yuvSize);
                reader = buildReader(
                        yuvSize,
                        ImageFormat.YUV_420_888, yuvMax, yuvUsage);
                actualStreams.put(streamJson("YUV_420_888", yuvSize, yuvMax));
                actualStreams.getJSONObject(actualStreams.length() - 1).put("usage", reader.getUsage());
                actualStreams.getJSONObject(actualStreams.length() - 1).put("hardwareBufferFormat", reader.getHardwareBufferFormat());
                actualStreams.getJSONObject(actualStreams.length() - 1).put("dataSpace", reader.getDataSpace());
            } else {
                throw new IllegalArgumentException("Unknown stream: " + streamName);
            }
            readers.add(reader);
            surfaces.add(reader.getSurface());
        }
        root.put("actualStreams", actualStreams);
        AtomicReference<CameraDevice> deviceRef = new AtomicReference<>();
        AtomicReference<CameraCaptureSession> sessionRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        long startNs = System.nanoTime();
        String finalCameraId = cameraId;

        manager.openCamera(cameraId, new CameraDevice.StateCallback() {
            @Override
            public void onOpened(CameraDevice camera) {
                deviceRef.set(camera);
                try {
                    CameraCaptureSession.StateCallback cb =
                            new CameraCaptureSession.StateCallback() {
                                @Override
                                public void onConfigured(CameraCaptureSession session) {
                                    sessionRef.set(session);
                                    done.countDown();
                                }

                                @Override
                                public void onConfigureFailed(CameraCaptureSession session) {
                                    sessionRef.set(session);
                                    errorRef.compareAndSet(null, "onConfigureFailed");
                                    done.countDown();
                                }
                            };

                    if (sessionCfg) {
                        List<OutputConfiguration> outputs = new ArrayList<>();
                        for (Surface surface : surfaces) {
                            OutputConfiguration output = new OutputConfiguration(surface);
                            if (pixelMode0) {
                                output.addSensorPixelModeUsed(
                                        CameraMetadata.SENSOR_PIXEL_MODE_DEFAULT);
                            }
                            outputs.add(output);
                        }
                        SessionConfiguration config = new SessionConfiguration(
                                sessionType,
                                outputs,
                                command -> cameraHandler.post(command),
                                cb);
                        if (clientName != null || videoStab >= 0 || sceneMode >= 0
                                || (fpsLower >= 0 && fpsUpper >= 0)) {
                            CaptureRequest.Builder builder =
                                    camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                            if (clientName != null) {
                                CaptureRequest.Key<String> key = new CaptureRequest.Key<>(
                                        "com.xiaomi.sessionparams.clientName", String.class);
                                builder.set(key, clientName);
                            }
                            if (videoStab >= 0) {
                                builder.set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE,
                                        videoStab);
                            }
                            if (sceneMode >= 0) {
                                builder.set(CaptureRequest.CONTROL_SCENE_MODE, sceneMode);
                            }
                            if (fpsLower >= 0 && fpsUpper >= 0) {
                                builder.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
                                        new Range<>(fpsLower, fpsUpper));
                            }
                            config.setSessionParameters(builder.build());
                        }
                        if (inputFormatName != null && !inputFormatName.isEmpty()) {
                            final int inputFormat;
                            if ("private".equalsIgnoreCase(inputFormatName)) {
                                inputFormat = ImageFormat.PRIVATE;
                            } else if ("yuv".equalsIgnoreCase(inputFormatName)
                                    || "yuv_420_888".equalsIgnoreCase(inputFormatName)) {
                                inputFormat = ImageFormat.YUV_420_888;
                            } else if ("raw10".equalsIgnoreCase(inputFormatName)) {
                                inputFormat = ImageFormat.RAW10;
                            } else if ("raw12".equalsIgnoreCase(inputFormatName)) {
                                inputFormat = ImageFormat.RAW12;
                            } else {
                                throw new IllegalArgumentException(
                                        "Unsupported inputFormat: " + inputFormatName);
                            }
                            Size inputSize = findExact(
                                    map.getInputSizes(inputFormat), inputWidth, inputHeight);
                            if (inputSize == null) {
                                throw new IllegalStateException(
                                        "Requested input is not advertised: "
                                                + inputFormatName + " "
                                                + inputWidth + "x" + inputHeight);
                            }
                            config.setInputConfiguration(new InputConfiguration(
                                    inputWidth, inputHeight, inputFormat));
                        }
                        camera.createCaptureSession(config);
                    } else {
                        camera.createCaptureSession(surfaces, cb, cameraHandler);
                    }
                } catch (Exception e) {
                    errorRef.compareAndSet(null, "createSession: " + e);
                    done.countDown();
                }
            }
            @Override
            public void onDisconnected(CameraDevice camera) {
                deviceRef.compareAndSet(null, camera);
                errorRef.compareAndSet(null, "disconnected");
                done.countDown();
            }

            @Override
            public void onError(CameraDevice camera, int error) {
                deviceRef.compareAndSet(null, camera);
                errorRef.compareAndSet(null, "cameraError=" + error);
                done.countDown();
            }
        }, cameraHandler);

        boolean completed = done.await(TIMEOUT_MS, TimeUnit.MILLISECONDS);
        long elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startNs);
        String error = errorRef.get();
        root.put("completed", completed);
        root.put("configured", completed && error == null && sessionRef.get() != null);
        root.put("elapsedMs", elapsedMs);
        root.put("error", error == null ? JSONObject.NULL : error);

        CameraCaptureSession session = sessionRef.get();
        if (session != null) session.close();
        CameraDevice device = deviceRef.get();
        if (device != null) device.close();
        for (ImageReader reader : readers) reader.close();
        return root;
    }
    @SuppressLint("NewApi")
    private static ImageReader buildReader(
            Size size, int format, int maxImages, long usage) {
        ImageReader.Builder builder =
                new ImageReader.Builder(size.getWidth(), size.getHeight())
                        .setImageFormat(format)
                        .setMaxImages(maxImages);
        if (usage >= 0L) builder.setUsage(usage);
        return builder.build();
    }

    private static void requireSize(String name, Size size) {
        if (size == null) throw new IllegalStateException(name + " size unavailable");
    }

    private static Size findExact(Size[] sizes, int width, int height) {
        if (sizes == null) return null;
        for (Size size : sizes) {
            if (size.getWidth() == width && size.getHeight() == height) return size;
        }
        return null;
    }

    private static Size largest(Size[] sizes) {
        if (sizes == null || sizes.length == 0) return null;
        Size best = sizes[0];
        long bestArea = (long) best.getWidth() * best.getHeight();
        for (Size size : sizes) {
            long area = (long) size.getWidth() * size.getHeight();
            if (area > bestArea) {
                best = size;
                bestArea = area;
            }
        }
        return best;
    }

    private static JSONObject streamJson(String format, Size size, int maxImages)
            throws Exception {
        return new JSONObject()
                .put("format", format)
                .put("width", size.getWidth())
                .put("height", size.getHeight())
                .put("maxImages", maxImages);
    }
}

package dev.alaa.pocof5.lensverifier;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Size;
import android.view.Surface;
import android.view.TextureView;
import android.widget.Button;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TimeZone;

public final class MainActivity extends Activity implements TextureView.SurfaceTextureListener {
    private static final int CAMERA_PERMISSION_REQUEST = 1001;
    private static final String EXPORT_NAME = "lens-mapping.json";

    private TextureView previewTexture;
    private TextView cameraInfoText;
    private TextView statusText;
    private Button previousButton;
    private Button nextButton;

    private CameraManager cameraManager;
    private String[] cameraIds = new String[0];
    private int selectedIndex = 0;

    private final Map<String, String> labels = new LinkedHashMap<>();

    private HandlerThread backgroundThread;
    private Handler backgroundHandler;

    private CameraDevice cameraDevice;
    private CameraCaptureSession captureSession;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        previewTexture = findViewById(R.id.previewTexture);
        cameraInfoText = findViewById(R.id.cameraInfoText);
        statusText = findViewById(R.id.statusText);
        previousButton = findViewById(R.id.previousButton);
        nextButton = findViewById(R.id.nextButton);

        previewTexture.setSurfaceTextureListener(this);

        cameraManager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);

        previousButton.setOnClickListener(v -> selectRelative(-1));
        nextButton.setOnClickListener(v -> selectRelative(1));

        findViewById(R.id.mainButton).setOnClickListener(v -> labelCurrent("MAIN"));
        findViewById(R.id.ultrawideButton).setOnClickListener(v -> labelCurrent("ULTRAWIDE"));
        findViewById(R.id.macroButton).setOnClickListener(v -> labelCurrent("MACRO"));
        findViewById(R.id.frontButton).setOnClickListener(v -> labelCurrent("FRONT"));
        findViewById(R.id.otherButton).setOnClickListener(v -> labelCurrent("OTHER_OR_DUPLICATE"));
        findViewById(R.id.clearButton).setOnClickListener(v -> clearCurrentLabel());
        findViewById(R.id.exportButton).setOnClickListener(v -> exportMapping());

        requestCameraPermissionIfNeeded();
    }

    @Override
    protected void onResume() {
        super.onResume();
        startBackgroundThread();
        if (hasCameraPermission()) {
            initializeCameraIds();
            if (previewTexture.isAvailable()) {
                openSelectedCamera();
            }
        }
    }

    @Override
    protected void onPause() {
        closeCamera();
        stopBackgroundThread();
        super.onPause();
    }

    private void requestCameraPermissionIfNeeded() {
        if (hasCameraPermission()) {
            initializeCameraIds();
            return;
        }

        requestPermissions(
                new String[]{Manifest.permission.CAMERA},
                CAMERA_PERMISSION_REQUEST
        );
    }

    private boolean hasCameraPermission() {
        return checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED;
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
            statusText.setText("Camera permission granted.");
            initializeCameraIds();
            if (previewTexture.isAvailable()) {
                openSelectedCamera();
            }
        } else {
            statusText.setText(
                    "Camera permission is required only for live lens verification. " +
                    "No photos or videos are recorded."
            );
        }
    }

    private void initializeCameraIds() {
        try {
            cameraIds = cameraManager.getCameraIdList();
            Arrays.sort(cameraIds);

            if (cameraIds.length == 0) {
                cameraInfoText.setText("No Camera2 IDs are exposed to this package.");
                statusText.setText("Nothing to verify.");
                setNavigationEnabled(false);
                return;
            }

            if (selectedIndex >= cameraIds.length) {
                selectedIndex = 0;
            }

            setNavigationEnabled(cameraIds.length > 1);
            updateCameraInfo();
        } catch (CameraAccessException e) {
            cameraInfoText.setText("Unable to enumerate Camera2 IDs.");
            statusText.setText("CameraAccessException: " + e.getMessage());
        }
    }

    private void setNavigationEnabled(boolean enabled) {
        previousButton.setEnabled(enabled);
        nextButton.setEnabled(enabled);
    }

    private void selectRelative(int delta) {
        if (cameraIds.length == 0) {
            return;
        }

        int next = (selectedIndex + delta) % cameraIds.length;
        if (next < 0) {
            next += cameraIds.length;
        }

        if (next == selectedIndex) {
            return;
        }

        selectedIndex = next;
        closeCamera();
        updateCameraInfo();

        if (hasCameraPermission() && previewTexture.isAvailable()) {
            openSelectedCamera();
        }
    }

    private String currentCameraId() {
        if (cameraIds.length == 0 || selectedIndex < 0 || selectedIndex >= cameraIds.length) {
            return null;
        }
        return cameraIds[selectedIndex];
    }

    private void labelCurrent(String role) {
        String id = currentCameraId();
        if (id == null) {
            return;
        }

        labels.put(id, role);
        updateCameraInfo();
        statusText.setText(
                "Camera ID " + id + " marked as " + role +
                ". Continue verifying every ID, then press Export verified mapping."
        );
    }

    private void clearCurrentLabel() {
        String id = currentCameraId();
        if (id == null) {
            return;
        }

        labels.remove(id);
        updateCameraInfo();
        statusText.setText("Label cleared for Camera ID " + id + ".");
    }

    private void updateCameraInfo() {
        String id = currentCameraId();
        if (id == null) {
            cameraInfoText.setText("No selected Camera ID.");
            return;
        }

        try {
            CameraCharacteristics c = cameraManager.getCameraCharacteristics(id);
            StringBuilder text = new StringBuilder();

            text.append("Camera ID: ").append(id)
                    .append("  (").append(selectedIndex + 1)
                    .append("/").append(cameraIds.length).append(")\n");

            String assigned = labels.get(id);
            text.append("Verified role: ")
                    .append(assigned == null ? "UNVERIFIED" : assigned)
                    .append("\n");

            Integer facing = c.get(CameraCharacteristics.LENS_FACING);
            text.append("Facing: ").append(lensFacingName(facing)).append("\n");

            Integer level = c.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL);
            text.append("Hardware level: ")
                    .append(hardwareLevelName(level)).append("\n");

            float[] focalLengths =
                    c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS);
            text.append("Focal length(s): ")
                    .append(focalLengths == null ? "—" : Arrays.toString(focalLengths))
                    .append(" mm\n");

            float[] apertures =
                    c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_APERTURES);
            text.append("Aperture(s): ")
                    .append(apertures == null ? "—" : Arrays.toString(apertures))
                    .append("\n");

            if (Build.VERSION.SDK_INT >= 28) {
                Set<String> physicalIds = c.getPhysicalCameraIds();
                text.append("Physical IDs: ")
                        .append(physicalIds.isEmpty() ? "—" : physicalIds)
                        .append("\n");
            }

            Size maxJpeg = findLargestJpeg(c);
            text.append("Max JPEG: ")
                    .append(maxJpeg == null
                            ? "—"
                            : maxJpeg.getWidth() + "x" + maxJpeg.getHeight())
                    .append("\n");

            text.append("\nVerification method:\n")
                    .append("Cover one physical lens at a time. ")
                    .append("Assign a role only when the live preview clearly confirms which lens is active.");

            cameraInfoText.setText(text.toString());
        } catch (CameraAccessException e) {
            cameraInfoText.setText(
                    "Failed to read Camera ID " + id + ": " + e.getMessage()
            );
        }
    }

    private void openSelectedCamera() {
        if (!hasCameraPermission() || backgroundHandler == null) {
            return;
        }

        String id = currentCameraId();
        if (id == null) {
            return;
        }

        SurfaceTexture texture = previewTexture.getSurfaceTexture();
        if (texture == null) {
            return;
        }

        try {
            CameraCharacteristics c = cameraManager.getCameraCharacteristics(id);
            Size previewSize = choosePreviewSize(c);
            if (previewSize != null) {
                texture.setDefaultBufferSize(
                        previewSize.getWidth(),
                        previewSize.getHeight()
                );
            }

            statusText.setText("Opening Camera ID " + id + "...");

            cameraManager.openCamera(
                    id,
                    new CameraDevice.StateCallback() {
                        @Override
                        public void onOpened(CameraDevice camera) {
                            cameraDevice = camera;
                            createPreviewSession();
                        }

                        @Override
                        public void onDisconnected(CameraDevice camera) {
                            camera.close();
                            if (cameraDevice == camera) {
                                cameraDevice = null;
                            }
                            runOnUiThread(() ->
                                    statusText.setText(
                                            "Camera ID " + id + " disconnected."
                                    ));
                        }

                        @Override
                        public void onError(CameraDevice camera, int error) {
                            camera.close();
                            if (cameraDevice == camera) {
                                cameraDevice = null;
                            }
                            runOnUiThread(() ->
                                    statusText.setText(
                                            "Failed to open Camera ID " + id +
                                            ". CameraDevice error=" + error
                                    ));
                        }
                    },
                    backgroundHandler
            );
        } catch (SecurityException e) {
            statusText.setText("Camera permission is missing.");
        } catch (CameraAccessException | IllegalArgumentException e) {
            statusText.setText(
                    "Camera ID " + id + " cannot be opened by this package: " +
                    e.getMessage()
            );
        }
    }

    private void createPreviewSession() {
        CameraDevice device = cameraDevice;
        SurfaceTexture texture = previewTexture.getSurfaceTexture();
        if (device == null || texture == null) {
            return;
        }

        Surface surface = new Surface(texture);

        try {
            CaptureRequest.Builder builder =
                    device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
            builder.addTarget(surface);
            builder.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);

            device.createCaptureSession(
                    Arrays.asList(surface),
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession session) {
                            if (cameraDevice == null) {
                                session.close();
                                return;
                            }

                            captureSession = session;
                            try {
                                session.setRepeatingRequest(
                                        builder.build(),
                                        null,
                                        backgroundHandler
                                );
                                String id = currentCameraId();
                                runOnUiThread(() ->
                                        statusText.setText(
                                                "Live preview active for Camera ID " + id +
                                                ". Cover the physical lenses to identify it."
                                        ));
                            } catch (CameraAccessException e) {
                                runOnUiThread(() ->
                                        statusText.setText(
                                                "Preview request failed: " + e.getMessage()
                                        ));
                            }
                        }

                        @Override
                        public void onConfigureFailed(CameraCaptureSession session) {
                            runOnUiThread(() ->
                                    statusText.setText(
                                            "Preview session could not be configured."
                                    ));
                        }
                    },
                    backgroundHandler
            );
        } catch (CameraAccessException e) {
            statusText.setText("Preview setup failed: " + e.getMessage());
        }
    }

    private Size choosePreviewSize(CameraCharacteristics c) {
        StreamConfigurationMap map =
                c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);

        if (map == null) {
            return null;
        }

        Size[] sizes = map.getOutputSizes(SurfaceTexture.class);
        if (sizes == null || sizes.length == 0) {
            return null;
        }

        Size best = null;
        long bestArea = -1L;

        for (Size size : sizes) {
            long area = (long) size.getWidth() * size.getHeight();

            if (size.getWidth() <= 1920
                    && size.getHeight() <= 1080
                    && area > bestArea) {
                best = size;
                bestArea = area;
            }
        }

        if (best != null) {
            return best;
        }

        return sizes[0];
    }

    private void closeCamera() {
        if (captureSession != null) {
            captureSession.close();
            captureSession = null;
        }

        if (cameraDevice != null) {
            cameraDevice.close();
            cameraDevice = null;
        }
    }

    private void startBackgroundThread() {
        if (backgroundThread != null) {
            return;
        }

        backgroundThread = new HandlerThread("LensVerifierCamera");
        backgroundThread.start();
        backgroundHandler = new Handler(backgroundThread.getLooper());
    }

    private void stopBackgroundThread() {
        HandlerThread thread = backgroundThread;
        backgroundThread = null;
        backgroundHandler = null;

        if (thread == null) {
            return;
        }

        thread.quitSafely();
        try {
            thread.join();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void exportMapping() {
        try {
            JSONObject root = new JSONObject();
            root.put("schemaVersion", 1);
            root.put("generatedAtUtc", utcNow());
            root.put("packageName", getPackageName());
            root.put("device", buildDeviceInfo());

            JSONArray ids = new JSONArray();
            JSONArray mappings = new JSONArray();

            for (String id : cameraIds) {
                ids.put(id);

                CameraCharacteristics c =
                        cameraManager.getCameraCharacteristics(id);

                JSONObject item = new JSONObject();
                item.put("cameraId", id);
                item.put(
                        "verifiedRole",
                        labels.containsKey(id)
                                ? labels.get(id)
                                : JSONObject.NULL
                );

                Integer facing = c.get(CameraCharacteristics.LENS_FACING);
                item.put("lensFacing", lensFacingName(facing));

                Integer level =
                        c.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL);
                item.put("hardwareLevel", hardwareLevelName(level));

                float[] focalLengths =
                        c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS);
                item.put("focalLengthsMm", floatArray(focalLengths));

                float[] apertures =
                        c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_APERTURES);
                item.put("apertures", floatArray(apertures));

                if (Build.VERSION.SDK_INT >= 28) {
                    String[] physicalIds =
                            c.getPhysicalCameraIds().toArray(new String[0]);
                    Arrays.sort(physicalIds);
                    item.put("physicalCameraIds", stringArray(physicalIds));
                }

                Size maxJpeg = findLargestJpeg(c);
                if (maxJpeg != null) {
                    item.put(
                            "maxJpeg",
                            new JSONObject()
                                    .put("width", maxJpeg.getWidth())
                                    .put("height", maxJpeg.getHeight())
                    );
                } else {
                    item.put("maxJpeg", JSONObject.NULL);
                }

                mappings.put(item);
            }

            root.put("cameraIds", ids);
            root.put("mappings", mappings);

            File output = new File(getFilesDir(), EXPORT_NAME);
            try (OutputStreamWriter writer = new OutputStreamWriter(
                    new FileOutputStream(output, false),
                    StandardCharsets.UTF_8)) {
                writer.write(root.toString(2));
                writer.write("\n");
            }

            int verifiedCount = labels.size();
            statusText.setText(
                    "Mapping exported successfully.\n" +
                    "Verified IDs: " + verifiedCount + "/" + cameraIds.length + "\n" +
                    "ADB export path: files/" + EXPORT_NAME
            );
        } catch (Exception e) {
            statusText.setText("Mapping export failed: " + e);
        }
    }

    private JSONObject buildDeviceInfo() throws Exception {
        JSONObject device = new JSONObject();
        device.put("manufacturer", Build.MANUFACTURER);
        device.put("brand", Build.BRAND);
        device.put("model", Build.MODEL);
        device.put("device", Build.DEVICE);
        device.put("product", Build.PRODUCT);
        device.put("buildId", Build.ID);
        device.put("display", Build.DISPLAY);
        device.put("fingerprint", Build.FINGERPRINT);
        device.put("androidRelease", Build.VERSION.RELEASE);
        device.put("sdkInt", Build.VERSION.SDK_INT);

        if (Build.VERSION.SDK_INT >= 23) {
            device.put("securityPatch", Build.VERSION.SECURITY_PATCH);
        }

        return device;
    }

    private static JSONArray floatArray(float[] values) throws Exception {
        JSONArray array = new JSONArray();
        if (values == null) {
            return array;
        }

        for (float value : values) {
            array.put(value);
        }
        return array;
    }

    private static JSONArray stringArray(String[] values) {
        JSONArray array = new JSONArray();
        for (String value : values) {
            array.put(value);
        }
        return array;
    }

    private static Size findLargestJpeg(CameraCharacteristics c) {
        StreamConfigurationMap map =
                c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);

        if (map == null) {
            return null;
        }

        Size[] sizes = map.getOutputSizes(ImageFormat.JPEG);
        if (sizes == null || sizes.length == 0) {
            return null;
        }

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

    private static String lensFacingName(Integer value) {
        if (value == null) {
            return "UNKNOWN";
        }

        switch (value) {
            case CameraCharacteristics.LENS_FACING_FRONT:
                return "FRONT";
            case CameraCharacteristics.LENS_FACING_BACK:
                return "BACK";
            case CameraCharacteristics.LENS_FACING_EXTERNAL:
                return "EXTERNAL";
            default:
                return "UNKNOWN_" + value;
        }
    }

    private static String hardwareLevelName(Integer value) {
        if (value == null) {
            return "UNKNOWN";
        }

        switch (value) {
            case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LEGACY:
                return "LEGACY";
            case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LIMITED:
                return "LIMITED";
            case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_FULL:
                return "FULL";
            case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_3:
                return "LEVEL_3";
            case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_EXTERNAL:
                return "EXTERNAL";
            default:
                return "UNKNOWN_" + value;
        }
    }

    private static String utcNow() {
        SimpleDateFormat format =
                new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }

    @Override
    public void onSurfaceTextureAvailable(
            SurfaceTexture surface,
            int width,
            int height) {
        if (hasCameraPermission()) {
            openSelectedCamera();
        }
    }

    @Override
    public void onSurfaceTextureSizeChanged(
            SurfaceTexture surface,
            int width,
            int height) {
    }

    @Override
    public boolean onSurfaceTextureDestroyed(SurfaceTexture surface) {
        closeCamera();
        return true;
    }

    @Override
    public void onSurfaceTextureUpdated(SurfaceTexture surface) {
    }
}

/*
 * Minimal host worker for Tencent's XP2P Android/Bionic C ABI.
 *
 * The worker intentionally has no Android framework or JNI dependency.  It is
 * built as an AArch64 Bionic executable and is launched by the Python client
 * either directly (Linux AArch64) or through qemu-aarch64 user mode (Linux
 * x86_64).  Sensitive runtime values travel only over stdin.
 *
 * Protocol (network byte order):
 *   request:  "DXP1", u32 field_count, repeated (u32 length, bytes),
 *             u64 command_timeout_us, u32 status_attempts,
 *             u32 status_retry_ms, u32 delegate_attempts,
 *             u32 delegate_retry_ms
 *   response: "DXR1", u32 status, u32 payload_length, payload bytes
 *
 * On success the payload is the local HTTP-FLV URL.  The worker then remains
 * alive until stdin closes or receives one byte, and performs XP2P cleanup.
 */

#include <dlfcn.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define FIELD_COUNT 20U
#define MAX_FIELD_LENGTH (1024U * 1024U)
#define RESPONSE_OK 0U
#define RESPONSE_BAD_REQUEST 1U
#define RESPONSE_LOAD_FAILED 2U
#define RESPONSE_SYMBOL_MISSING 3U
#define RESPONSE_QCLOUD_FAILED 10U
#define RESPONSE_START_FAILED 11U
#define RESPONSE_P2P_INFO_FAILED 12U
#define RESPONSE_STATUS_COMMAND_FAILED 13U
#define RESPONSE_DEVICE_NOT_READY 14U
#define RESPONSE_AV_RECV_FAILED 15U
#define RESPONSE_DELEGATE_FAILED 16U
#define RESPONSE_READY_EVENT_FAILED 17U
#define XP2P_PROTOCOL_AUTO 0
#define XP2P_STUN_PORT 20002U

static int response_fd = STDOUT_FILENO;
static volatile int last_xp2p_event = 0;
static void trace(const char *message);

enum request_field {
    FIELD_LIBRARY_PATH = 0,
    FIELD_STUN_FILE_PATH,
    FIELD_SERVICE_ID,
    FIELD_DELEGATE_ID,
    FIELD_PRODUCT_ID,
    FIELD_DEVICE_NAME,
    FIELD_P2P_INFO,
    FIELD_SECRET_ID,
    FIELD_SECRET_KEY,
    FIELD_FLV_PATH,
    FIELD_DEVICE_STATUS_COMMAND,
    FIELD_LIVE_COMMAND,
    FIELD_CONFIG_SERVER,
    FIELD_CONFIG_IP,
    FIELD_CONFIG_PORT,
    FIELD_CONFIG_PROTOCOL,
    FIELD_CONFIG_CROSS,
    FIELD_TRANSPORT,
    FIELD_LAN_ADDRESS,
    FIELD_LAN_PORT
};

struct xp2p_app_config {
    const char *server;
    const char *ip;
    uint64_t port;
    int type;
    bool cross;
};

/* The pinned Tencent XP2P 2.4.50 startService takes the integer protocol mode.
 * The large-page 2.4.71 runtime takes p2p_info plus an app_config pointer.
 * setCrossStunTurn distinguishes those two pinned ABIs at runtime. */
typedef int (*start_service_legacy_fn)(const char *, const char *, const char *,
                                       int);
typedef int (*start_service_config_fn)(const char *, const char *, const char *,
                                       const char *,
                                       const struct xp2p_app_config *);
typedef int (*start_lan_service_fn)(const char *, const char *, const char *,
                                    const char *, const char *);
typedef int (*set_device_info_fn)(const char *, const char *);
typedef int (*post_command_fn)(const char *, const unsigned char *, size_t,
                               unsigned char **, size_t *, uint64_t);
typedef void *(*start_av_recv_fn)(const char *, const char *, bool);
typedef int (*stop_av_recv_fn)(const char *, void *);
typedef const char *(*delegate_http_flv_fn)(const char *);
typedef const char *(*get_lan_url_fn)(const char *);
typedef int (*get_lan_proxy_port_fn)(const char *);
typedef int (*get_stream_link_mode_fn)(const char *);
typedef void (*stop_service_fn)(const char *);
typedef int (*set_qcloud_cred_fn)(const char *, const char *);
typedef void (*set_stun_server_fn)(const char *, uint16_t);
typedef void (*set_cross_stun_turn_fn)(bool);
typedef void (*av_receive_callback_fn)(const char *, uint8_t *, size_t);
typedef const char *(*message_callback_fn)(const char *, int, const char *);
typedef char *(*device_data_callback_fn)(const char *, uint8_t *, size_t);
typedef void (*set_user_callback_fn)(av_receive_callback_fn,
                                     message_callback_fn,
                                     device_data_callback_fn);

struct request {
    char *fields[FIELD_COUNT];
    uint64_t command_timeout_us;
    uint32_t status_attempts;
    uint32_t status_retry_ms;
    uint32_t delegate_attempts;
    uint32_t delegate_retry_ms;
};

static void receive_callback(const char *id, uint8_t *data, size_t length) {
    (void)id;
    (void)data;
    (void)length;
}

static const char *message_callback(const char *id, int type,
                                    const char *message) {
    (void)id;
    (void)message;
    if (type == 1004) {
        last_xp2p_event = type;
        trace("xp2p-worker: ready event received");
    } else if (type == 1005) {
        last_xp2p_event = type;
        trace("xp2p-worker: detect error event received");
    }
    return "";
}

static char *device_data_callback(const char *id, uint8_t *data,
                                  size_t length) {
    static char response[] = "app reply to device";
    (void)id;
    (void)data;
    (void)length;
    return response;
}

static int read_exact(void *buffer, size_t length) {
    unsigned char *cursor = (unsigned char *)buffer;
    while (length > 0) {
        ssize_t count = read(STDIN_FILENO, cursor, length);
        if (count <= 0) {
            return -1;
        }
        cursor += (size_t)count;
        length -= (size_t)count;
    }
    return 0;
}

static int write_exact(const void *buffer, size_t length) {
    const unsigned char *cursor = (const unsigned char *)buffer;
    while (length > 0) {
        ssize_t count = write(response_fd, cursor, length);
        if (count <= 0) {
            return -1;
        }
        cursor += (size_t)count;
        length -= (size_t)count;
    }
    return 0;
}

static void trace(const char *message) {
    size_t length = strlen(message);
    (void)write(STDERR_FILENO, message, length);
    (void)write(STDERR_FILENO, "\n", 1U);
}

static uint32_t decode_u32(const unsigned char value[4]) {
    return ((uint32_t)value[0] << 24) | ((uint32_t)value[1] << 16) |
           ((uint32_t)value[2] << 8) | (uint32_t)value[3];
}

static uint64_t decode_u64(const unsigned char value[8]) {
    uint64_t result = 0;
    unsigned int index;
    for (index = 0; index < 8; index++) {
        result = (result << 8) | (uint64_t)value[index];
    }
    return result;
}

static uint64_t parse_unsigned(const char *value, uint64_t fallback) {
    uint64_t result = 0;
    if (value == NULL || *value == 0) {
        return fallback;
    }
    while (*value != 0) {
        if (*value < '0' || *value > '9') {
            return fallback;
        }
        result = result * 10U + (uint64_t)(*value - '0');
        value++;
    }
    return result;
}

static void encode_u32(unsigned char value[4], uint32_t number) {
    value[0] = (unsigned char)(number >> 24);
    value[1] = (unsigned char)(number >> 16);
    value[2] = (unsigned char)(number >> 8);
    value[3] = (unsigned char)number;
}

static int read_u32(uint32_t *result) {
    unsigned char value[4];
    if (read_exact(value, sizeof(value)) != 0) {
        return -1;
    }
    *result = decode_u32(value);
    return 0;
}

static int read_u64(uint64_t *result) {
    unsigned char value[8];
    if (read_exact(value, sizeof(value)) != 0) {
        return -1;
    }
    *result = decode_u64(value);
    return 0;
}

static int send_response(uint32_t status, const char *payload) {
    static const unsigned char magic[] = {'D', 'X', 'R', '1'};
    unsigned char number[4];
    size_t length = payload == NULL ? 0 : strlen(payload);
    if (length > UINT32_MAX) {
        return -1;
    }
    if (write_exact(magic, sizeof(magic)) != 0) {
        return -1;
    }
    encode_u32(number, status);
    if (write_exact(number, sizeof(number)) != 0) {
        return -1;
    }
    encode_u32(number, (uint32_t)length);
    if (write_exact(number, sizeof(number)) != 0) {
        return -1;
    }
    return length == 0 || write_exact(payload, length) == 0 ? 0 : -1;
}

static void secure_zero(char *value) {
    volatile unsigned char *cursor;
    if (value == NULL) {
        return;
    }
    cursor = (volatile unsigned char *)value;
    while (*cursor != 0) {
        *cursor++ = 0;
    }
}

static void free_request(struct request *request) {
    uint32_t index;
    secure_zero(request->fields[FIELD_P2P_INFO]);
    secure_zero(request->fields[FIELD_SECRET_ID]);
    secure_zero(request->fields[FIELD_SECRET_KEY]);
    for (index = 0; index < FIELD_COUNT; index++) {
        free(request->fields[index]);
        request->fields[index] = NULL;
    }
}

static int read_request(struct request *request) {
    static const unsigned char expected_magic[] = {'D', 'X', 'P', '1'};
    unsigned char magic[sizeof(expected_magic)];
    uint32_t field_count;
    uint32_t index;
    memset(request, 0, sizeof(*request));
    if (read_exact(magic, sizeof(magic)) != 0 ||
        memcmp(magic, expected_magic, sizeof(magic)) != 0 ||
        read_u32(&field_count) != 0 || field_count != FIELD_COUNT) {
        return -1;
    }
    for (index = 0; index < FIELD_COUNT; index++) {
        uint32_t length;
        if (read_u32(&length) != 0 || length > MAX_FIELD_LENGTH) {
            return -1;
        }
        request->fields[index] = (char *)malloc((size_t)length + 1U);
        if (request->fields[index] == NULL ||
            (length > 0 && read_exact(request->fields[index], length) != 0)) {
            return -1;
        }
        request->fields[index][length] = 0;
    }
    if (read_u64(&request->command_timeout_us) != 0 ||
        read_u32(&request->status_attempts) != 0 ||
        read_u32(&request->status_retry_ms) != 0 ||
        read_u32(&request->delegate_attempts) != 0 ||
        read_u32(&request->delegate_retry_ms) != 0) {
        return -1;
    }
    if (request->fields[FIELD_LIBRARY_PATH][0] == 0 ||
        request->fields[FIELD_TRANSPORT][0] == 0) {
        return -1;
    }
    if (strcmp(request->fields[FIELD_TRANSPORT], "probe") == 0) {
        return 0;
    }
    if (request->fields[FIELD_SERVICE_ID][0] == 0 ||
        request->fields[FIELD_DELEGATE_ID][0] == 0 ||
        request->fields[FIELD_PRODUCT_ID][0] == 0 ||
        request->fields[FIELD_DEVICE_NAME][0] == 0 ||
        request->fields[FIELD_FLV_PATH][0] == 0) {
        return -1;
    }
    if (strcmp(request->fields[FIELD_TRANSPORT], "lan") == 0) {
        if (request->fields[FIELD_LAN_ADDRESS][0] == 0 ||
            request->fields[FIELD_LAN_PORT][0] == 0) {
            return -1;
        }
    } else if (strcmp(request->fields[FIELD_TRANSPORT], "cloud") == 0) {
        if (request->fields[FIELD_P2P_INFO][0] == 0 ||
            request->fields[FIELD_DEVICE_STATUS_COMMAND][0] == 0) {
            return -1;
        }
    } else {
        return -1;
    }
    if (request->status_attempts == 0) {
        request->status_attempts = 1;
    }
    if (request->delegate_attempts == 0) {
        request->delegate_attempts = 1;
    }
    return 0;
}

static int response_status_code(const unsigned char *response, size_t length,
                                int *status_code) {
    static const char key[] = "\"status\"";
    size_t index;
    if (response == NULL || length < sizeof(key)) {
        return -1;
    }
    for (index = 0; index + sizeof(key) - 1U < length; index++) {
        size_t cursor;
        int value = 0;
        int sign = 1;
        if (memcmp(response + index, key, sizeof(key) - 1U) != 0) {
            continue;
        }
        cursor = index + sizeof(key) - 1U;
        while (cursor < length &&
               (response[cursor] == ' ' || response[cursor] == '\t' ||
                response[cursor] == '\r' || response[cursor] == '\n' ||
                response[cursor] == ':')) {
            cursor++;
        }
        if (cursor < length && response[cursor] == '"') {
            cursor++;
        }
        if (cursor < length && response[cursor] == '-') {
            sign = -1;
            cursor++;
        }
        if (cursor >= length || response[cursor] < '0' ||
            response[cursor] > '9') {
            return -1;
        }
        while (cursor < length && response[cursor] >= '0' &&
               response[cursor] <= '9') {
            value = value * 10 + (response[cursor] - '0');
            cursor++;
        }
        *status_code = value * sign;
        return 0;
    }
    return -1;
}

static char *append_flv_path(const char *prefix, const char *path) {
    size_t prefix_length = strlen(prefix);
    const char *path_cursor = path;
    size_t path_length;
    bool slash;
    char *result;
    if (strstr(prefix, "ipc.flv") != NULL) {
        result = (char *)malloc(prefix_length + 1U);
        if (result != NULL) {
            memcpy(result, prefix, prefix_length + 1U);
        }
        return result;
    }
    while (*path_cursor == '/') {
        path_cursor++;
    }
    path_length = strlen(path_cursor);
    slash = prefix_length > 0 && prefix[prefix_length - 1U] != '/';
    result = (char *)malloc(prefix_length + (slash ? 1U : 0U) + path_length + 1U);
    if (result == NULL) {
        return NULL;
    }
    memcpy(result, prefix, prefix_length);
    if (slash) {
        result[prefix_length++] = '/';
    }
    memcpy(result + prefix_length, path_cursor, path_length + 1U);
    return result;
}

static char *append_lan_proxy_port(const char *prefix, const char *path,
                                   int proxy_port) {
    static const char suffix[] = "&_port=";
    char digits[6];
    size_t digit_count = 0;
    size_t base_length;
    uint32_t value;
    char *base;
    char *result;
    size_t index;
    if (proxy_port <= 0 || proxy_port > 65535) {
        return NULL;
    }
    base = append_flv_path(prefix, path);
    if (base == NULL) {
        return NULL;
    }
    value = (uint32_t)proxy_port;
    do {
        digits[digit_count++] = (char)('0' + value % 10U);
        value /= 10U;
    } while (value > 0U && digit_count < sizeof(digits));
    base_length = strlen(base);
    result = (char *)malloc(base_length + sizeof(suffix) - 1U + digit_count + 1U);
    if (result == NULL) {
        free(base);
        return NULL;
    }
    memcpy(result, base, base_length);
    memcpy(result + base_length, suffix, sizeof(suffix) - 1U);
    for (index = 0; index < digit_count; index++) {
        result[base_length + sizeof(suffix) - 1U + index] =
            digits[digit_count - index - 1U];
    }
    result[base_length + sizeof(suffix) - 1U + digit_count] = 0;
    free(base);
    return result;
}

static void sleep_ms(uint32_t milliseconds) {
    if (milliseconds > 0) {
        usleep((useconds_t)milliseconds * 1000U);
    }
}

static int run(void) {
    struct request request;
    struct xp2p_app_config app_config;
    void *library = NULL;
    void *av_handle = NULL;
    bool service_started = false;
    char *stream_url = NULL;
    uint32_t response_status = RESPONSE_OK;
    const char *response_payload = NULL;
    void *start_service;
    set_device_info_fn set_device_info;
    post_command_fn post_command;
    start_av_recv_fn start_av_recv;
    stop_av_recv_fn stop_av_recv;
    delegate_http_flv_fn delegate_http_flv;
    stop_service_fn stop_service;
    set_qcloud_cred_fn set_qcloud_cred;
    set_stun_server_fn set_stun_server;
    set_cross_stun_turn_fn set_cross_stun_turn;
    set_user_callback_fn set_user_callback;
    uint32_t attempt;
    int last_command_result = 0;
    int last_device_status = -1;
    bool lan_transport;
    bool probe_transport;
    start_lan_service_fn start_lan_service;
    get_lan_url_fn get_lan_url;
    get_lan_proxy_port_fn get_lan_proxy_port;
    get_stream_link_mode_fn get_stream_link_mode;
    char *success_payload = NULL;

    if (read_request(&request) != 0) {
        send_response(RESPONSE_BAD_REQUEST, "Invalid XP2P worker request.");
        free_request(&request);
        return 1;
    }

    /* Tencent writes native diagnostic messages to stdout when no app callback
     * is registered. Preserve stdout for the control response, then route those
     * messages to stderr so they cannot corrupt the binary protocol. */
    response_fd = dup(STDOUT_FILENO);
    if (response_fd < 0 || dup2(STDERR_FILENO, STDOUT_FILENO) < 0) {
        send_response(RESPONSE_BAD_REQUEST, "Could not initialize worker I/O.");
        free_request(&request);
        return 1;
    }

    app_config.server = request.fields[FIELD_CONFIG_SERVER];
    app_config.ip = request.fields[FIELD_CONFIG_IP];
    app_config.port = parse_unsigned(request.fields[FIELD_CONFIG_PORT],
                                     XP2P_STUN_PORT);
    app_config.type = (int)parse_unsigned(request.fields[FIELD_CONFIG_PROTOCOL],
                                          XP2P_PROTOCOL_AUTO);
    app_config.cross =
        parse_unsigned(request.fields[FIELD_CONFIG_CROSS], 0U) == 1U;
    lan_transport = strcmp(request.fields[FIELD_TRANSPORT], "lan") == 0;
    probe_transport = strcmp(request.fields[FIELD_TRANSPORT], "probe") == 0;
    last_xp2p_event = 0;

    trace("xp2p-worker: request accepted");
    library = dlopen(request.fields[FIELD_LIBRARY_PATH], RTLD_NOW | RTLD_GLOBAL);
    if (library == NULL) {
        const char *loader_error = dlerror();

        if (loader_error != NULL) {
            trace(loader_error);
        }
        response_status = RESPONSE_LOAD_FAILED;
        response_payload = "Could not load the XP2P runtime.";
        goto finish;
    }
    trace("xp2p-worker: runtime loaded");

    start_service = dlsym(library, "startService");
    start_lan_service =
        (start_lan_service_fn)dlsym(library, "startLanService");
    set_device_info = (set_device_info_fn)dlsym(library, "setDeviceXp2pInfo");
    post_command = (post_command_fn)dlsym(library, "postCommandRequestSync");
    start_av_recv = (start_av_recv_fn)dlsym(library, "startAvRecvService");
    delegate_http_flv =
        (delegate_http_flv_fn)dlsym(library, "delegateHttpFlv");
    get_lan_url = (get_lan_url_fn)dlsym(library, "getLanUrl");
    get_lan_proxy_port =
        (get_lan_proxy_port_fn)dlsym(library, "getLanProxyPort");
    get_stream_link_mode =
        (get_stream_link_mode_fn)dlsym(library, "getStreamLinkMode");
    if (((!lan_transport || probe_transport) &&
         (start_service == NULL || set_device_info == NULL ||
          post_command == NULL || delegate_http_flv == NULL)) ||
        (lan_transport && !probe_transport &&
         (start_lan_service == NULL || get_lan_url == NULL ||
          get_lan_proxy_port == NULL))) {
        response_status = RESPONSE_SYMBOL_MISSING;
        response_payload = "The XP2P runtime is missing required symbols.";
        goto finish;
    }
    trace("xp2p-worker: symbols resolved");
    stop_av_recv = (stop_av_recv_fn)dlsym(library, "stopAvRecvService");
    stop_service = (stop_service_fn)dlsym(library, "stopService");
    set_qcloud_cred = (set_qcloud_cred_fn)dlsym(library, "setQcloudApiCred");
    set_stun_server = (set_stun_server_fn)dlsym(library, "setStunServerToXp2p");
    set_cross_stun_turn =
        (set_cross_stun_turn_fn)dlsym(library, "setCrossStunTurn");
    set_user_callback =
        (set_user_callback_fn)dlsym(library, "setUserCallbackToXp2p");

    if (set_user_callback == NULL) {
        response_status = RESPONSE_SYMBOL_MISSING;
        response_payload = "The XP2P runtime is missing callback support.";
        goto finish;
    }
    if (probe_transport) {
        trace("xp2p-worker: runtime probe ready");
        response_payload = "XP2P runtime probe ready.";
        goto finish;
    }
    set_user_callback(receive_callback, message_callback, device_data_callback);

    if (!lan_transport && set_stun_server != NULL &&
        request.fields[FIELD_STUN_FILE_PATH][0] != 0) {
        set_stun_server(request.fields[FIELD_STUN_FILE_PATH],
                        (uint16_t)app_config.port);
    }
    if (!lan_transport && set_cross_stun_turn != NULL) {
        set_cross_stun_turn(app_config.cross);
    }
    if (!lan_transport && set_qcloud_cred != NULL &&
        request.fields[FIELD_SECRET_ID][0] != 0 &&
        request.fields[FIELD_SECRET_KEY][0] != 0 &&
        set_qcloud_cred(request.fields[FIELD_SECRET_ID],
                        request.fields[FIELD_SECRET_KEY]) != 0) {
        response_status = RESPONSE_QCLOUD_FAILED;
        response_payload = "XP2P QCloud credential setup failed.";
        goto finish;
    }
    trace("xp2p-worker: optional configuration applied");

    if (lan_transport) {
        last_command_result = start_lan_service(
            request.fields[FIELD_SERVICE_ID], request.fields[FIELD_PRODUCT_ID],
            request.fields[FIELD_DEVICE_NAME],
            request.fields[FIELD_LAN_ADDRESS], request.fields[FIELD_LAN_PORT]);
    } else {
        if (set_cross_stun_turn != NULL) {
            last_command_result = ((start_service_config_fn)start_service)(
                request.fields[FIELD_SERVICE_ID],
                request.fields[FIELD_PRODUCT_ID],
                request.fields[FIELD_DEVICE_NAME],
                request.fields[FIELD_P2P_INFO], &app_config);
        } else {
            last_command_result = ((start_service_legacy_fn)start_service)(
                request.fields[FIELD_SERVICE_ID],
                request.fields[FIELD_PRODUCT_ID],
                request.fields[FIELD_DEVICE_NAME], app_config.type);
        }
    }
    if (last_command_result != 0) {
        response_status = RESPONSE_START_FAILED;
        response_payload = "XP2P service startup failed.";
        goto finish;
    }
    service_started = true;
    trace(lan_transport ? "xp2p-worker: LAN service started"
                        : "xp2p-worker: service started");
    if (!lan_transport) {
        last_command_result = set_device_info(request.fields[FIELD_SERVICE_ID],
                                              request.fields[FIELD_P2P_INFO]);
        if (last_command_result != 0) {
            response_status = RESPONSE_P2P_INFO_FAILED;
            response_payload = "XP2P device configuration failed.";
            goto finish;
        }
        trace("xp2p-worker: device configuration applied");
    }

    for (attempt = 0; attempt < request.status_attempts; attempt++) {
        if (last_xp2p_event == 1004 || last_xp2p_event == 1005) {
            break;
        }
        sleep_ms(request.status_retry_ms);
    }
    if (last_xp2p_event != 1004) {
        response_status = RESPONSE_READY_EVENT_FAILED;
        response_payload = last_xp2p_event == 1005
                               ? "XP2P reported a native detect error."
                               : "XP2P did not emit its ready event.";
        goto finish;
    }

    if (!lan_transport) {
        for (attempt = 0; attempt < request.status_attempts; attempt++) {
            unsigned char *command_response = NULL;
            size_t response_length = 0;
            const char *command = request.fields[FIELD_DEVICE_STATUS_COMMAND];
            last_command_result = post_command(
                request.fields[FIELD_DELEGATE_ID],
                (const unsigned char *)command, strlen(command),
                &command_response, &response_length,
                request.command_timeout_us);
            last_device_status = -1;
            if (last_command_result == 0 &&
                response_status_code(command_response, response_length,
                                     &last_device_status) == 0 &&
                last_device_status == 0) {
                break;
            }
            if (attempt + 1U < request.status_attempts) {
                sleep_ms(request.status_retry_ms);
            }
        }
        if (last_command_result != 0) {
            response_status = RESPONSE_STATUS_COMMAND_FAILED;
            response_payload = "XP2P device status command failed.";
            goto finish;
        }
        if (last_device_status != 0) {
            response_status = RESPONSE_DEVICE_NOT_READY;
            response_payload = "The mower XP2P channel did not become ready.";
            goto finish;
        }
        trace("xp2p-worker: device ready");
    }

    /* The A2's proven Tencent 2.4.50 flow delegates HTTP-FLV directly after
     * readiness. Its startAvRecvService export returns no usable handle and is
     * not called by Dreame's working Java path. */
    (void)start_av_recv;

    for (attempt = 0; attempt < request.delegate_attempts; attempt++) {
        const char *prefix =
            lan_transport ? get_lan_url(request.fields[FIELD_SERVICE_ID])
                          : delegate_http_flv(request.fields[FIELD_DELEGATE_ID]);
        if (prefix != NULL && prefix[0] != 0) {
            stream_url =
                lan_transport
                    ? append_lan_proxy_port(
                          prefix, request.fields[FIELD_FLV_PATH],
                          get_lan_proxy_port(request.fields[FIELD_SERVICE_ID]))
                    : append_flv_path(prefix, request.fields[FIELD_FLV_PATH]);
            if (stream_url != NULL) {
                break;
            }
        }
        if (attempt + 1U < request.delegate_attempts) {
            sleep_ms(request.delegate_retry_ms);
        }
    }
    if (stream_url == NULL) {
        response_status = RESPONSE_DELEGATE_FAILED;
        response_payload = "XP2P did not expose a local FLV URL.";
        goto finish;
    }
    {
        const char *link_mode_text = "0\n";
        size_t link_mode_length = 2U;
        size_t stream_url_length = strlen(stream_url);
        int link_mode = lan_transport ? 62 : 0;

        if (!lan_transport && get_stream_link_mode != NULL) {
            link_mode =
                get_stream_link_mode(request.fields[FIELD_SERVICE_ID]);
            if (link_mode == 0 &&
                strcmp(request.fields[FIELD_DELEGATE_ID],
                       request.fields[FIELD_SERVICE_ID]) != 0) {
                link_mode =
                    get_stream_link_mode(request.fields[FIELD_DELEGATE_ID]);
            }
        }
        if (link_mode == 62) {
            link_mode_text = "62\n";
            link_mode_length = 3U;
        } else if (link_mode == 63) {
            link_mode_text = "63\n";
            link_mode_length = 3U;
        }
        success_payload = malloc(link_mode_length + stream_url_length + 1U);
        if (success_payload == NULL) {
            response_status = RESPONSE_DELEGATE_FAILED;
            response_payload = "XP2P could not describe the FLV route.";
            goto finish;
        }
        memcpy(success_payload, link_mode_text, link_mode_length);
        memcpy(success_payload + link_mode_length, stream_url,
               stream_url_length + 1U);
        response_payload = success_payload;
    }
    trace(lan_transport ? "xp2p-worker: LAN FLV proxy ready"
                        : "xp2p-worker: FLV delegate ready");

finish:
    send_response(response_status, response_payload);
    if (response_status == RESPONSE_OK) {
        unsigned char control_byte;
        while (read(STDIN_FILENO, &control_byte, 1U) == 1) {
            if (control_byte == 'Q') {
                int link_mode = lan_transport ? 62 : 0;
                if (!lan_transport && get_stream_link_mode != NULL) {
                    link_mode = get_stream_link_mode(
                        request.fields[FIELD_SERVICE_ID]);
                    if (link_mode == 0 &&
                        strcmp(request.fields[FIELD_DELEGATE_ID],
                               request.fields[FIELD_SERVICE_ID]) != 0) {
                        link_mode = get_stream_link_mode(
                            request.fields[FIELD_DELEGATE_ID]);
                    }
                }
                send_response(RESPONSE_OK,
                              link_mode == 62 ? "62"
                              : link_mode == 63 ? "63"
                                                : "0");
                continue;
            }
            break;
        }
    }
    if (av_handle != NULL && stop_av_recv != NULL) {
        stop_av_recv(request.fields[FIELD_DELEGATE_ID], av_handle);
    }
    if (service_started && stop_service != NULL) {
        stop_service(request.fields[FIELD_SERVICE_ID]);
    }
    free(stream_url);
    free(success_payload);
    free_request(&request);
    return response_status == RESPONSE_OK ? 0 : (int)response_status;
}

__attribute__((noreturn)) void _start(void) {
    int result = run();
    __asm__ volatile("mov x8, #93\n"
                     "svc #0\n"
                     :
                     : "r"(result)
                     : "x8");
    __builtin_unreachable();
}

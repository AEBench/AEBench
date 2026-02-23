/**
 * Common Definition of Neutrino Hooked Driver
 * 
 * @note Keep common.h only Linux/GNU dependencies, no other platform-specifics
 */
#include <unistd.h>   // for many thing
#include <stdlib.h>   // for standard library
#include <stdio.h>    // for file dump
#include <time.h>     // for timing
#include <dlfcn.h>    // for loading real shared library
#include <stdint.h>   // for uint64_t defn
#include <stdbool.h>  // for true false
#include <elf.h>      // for ELF Header
#include <sys/wait.h> // for waiting subprocess
#include <sys/stat.h> // for directory
#include <pthread.h>  // for mutex lock
#include "uthash.h"   // for hashmap
#include "sha1.h"     // for SHA1 hash algorithm

/**
 * @todo change probe type to enum for better portability
 * @todo standardize trace saving, got duplicate codes in cuda.c / hip.c
 * @todo standardize JIT interaction, got duplicate codes in cuda.c / hip.c
 */

#define PROBE_TYPE_THREAD 0
#define PROBE_TYPE_WARP 1
#define CDIV(a,b) (a + b - 1) / (b)

static FILE* event_log; // file pointer to event_log:  NEUTRINO_TRACEDIR/MM_DD_HH_MM_SS/event.event_log

/**
 * System Configuration and Setup
 */

static void* shared_lib           = NULL; // handle to real cuda driver
static char* NEUTRINO_REAL_DRIVER = NULL; // path to real cuda driver, loaded by env_var NEUTRINO_REAL_DRIVER
static char* NEUTRINO_PYTHON      = NULL; // path to python exe, loaded by env_var NEUTRINO_PYTHON
static char* NEUTRINO_PROBING_PY  = NULL; // path to process.py, loaded by env_var NEUTRINO_PROBING_PY
static char* NEUTRINO_CALLBACK    = NULL; // callback to analyze the kernel

// directory structure 
static char* RESULT_DIR = NULL; // env_var NEUTRINO_TRACEDIR/MM_DD_HH_MM_SS/result
static char* KERNEL_DIR = NULL; // env_var NEUTRINO_TRACEDIR/MM_DD_HH_MM_SS/kernel

/**
 * Benchmark mode, will include an additional launch after the trace kernel
 * Used to measure the kernel-level slowdown of Neutrino, disabled by default
 * @warning might cause CUDA_ERROR with in-place kernels, coupled with --filter if encountered
 *          this intrinsic of program and can not be resolved by Neutrino
 * @note benchmark_mem is a 256MB empty memory that will be cuMemSetD32 to 0
 *       which take the L2 Cache Space and Remove Previous L2 Cache Value, 
 * @cite this is inspired by Triton do_bench and Nvidia https://github.com/NVIDIA/nvbench/
 */
static int NEUTRINO_BENCHMARK = 0;
static size_t NEUTRINO_BENCHMARK_FLUSH_MEM_SIZE = 256e6; 

/**
 * A feature to measure the memory usage of probes other than really launch the
 * profiling kernel. Useful in debugging / preventing out-of-memory errors.
 */
static int NEUTRINO_MEMUSAGE = 0;

// simple auto-increasing idx to distinguish kernels of the same name
static int kernel_idx = 0;

// start time for event_logging. Neutrino trace are named as time since start
static struct timespec start;

// verbose setting -> to prevent event_log file too large due to unimportant setting
static int VERBOSE = 0; 

// dynamic setting -> enable it leads to a count kernel launched to detect the dynamic part
static int DYNAMIC = 0;

// helper macro to check dlopen/dlsym error
#define CHECK_DL() do {                    \
    const char *dl_error = dlerror();      \
    if (dl_error) {                        \
        fprintf(stderr, "%s\n", dl_error); \
        exit(EXIT_FAILURE);                \
    }                                      \
} while (0)

// utilities to get the trace folder name
char* get_tracedir() {
    // First read the parent directory
    char* NEUTRINO_TRACEDIR = getenv("NEUTRINO_TRACEDIR");
    if (NEUTRINO_TRACEDIR == NULL) {
        fprintf(stderr, "Environment Variable NEUTRINO_TRACEDIR not set\n");
        exit(EXIT_FAILURE);
    }
    // check and create folder structure
    // first create NEUTRINO_TRACE_DIR
    if (access(NEUTRINO_TRACEDIR, F_OK) != 0) { // not existed or bugs
        if (mkdir(NEUTRINO_TRACEDIR, 0755) != 0) {
            perror("Can not create NEUTRINO_TRACEDIR");
            exit(EXIT_FAILURE);
        }
    }

    unsigned long long start_time_jiffies;
    unsigned long uptime_seconds;

    // 1. read the 22nd value of /proc/[pid]/stat (jiffies of proc start time)
    FILE *stat_file = fopen("/proc/self/stat", "r");
    if (!stat_file) {
        perror("Failed to open /proc/[pid]/stat");
        exit(1);
    }
    for (int i = 0; i < 21; i++) {
        if (fscanf(stat_file, "%*s") == EOF) {
            fclose(stat_file);
            fprintf(stderr, "Invalid /proc/self/stat format\n");
            exit(1);
        }
    }
    int read_items = fscanf(stat_file, "%llu", &start_time_jiffies);
    fclose(stat_file);

    // 2. get system clock frequency (Hz, usually 100MHz)
    long clk_tck = sysconf(_SC_CLK_TCK);
    if (clk_tck <= 0) {
        fprintf(stderr, "Failed to get system clock tick\n");
        exit(1);
    }

    // 3. read the systme boot time (second, since 1970)
    FILE *uptime_file = fopen("/proc/uptime", "r");
    if (!uptime_file) {
        perror("Failed to open /proc/uptime");
        exit(1);
    }
    read_items = fscanf(uptime_file, "%lu", &uptime_seconds);
    fclose(uptime_file);

    // 4. compute absolute timestamp of proc boot time and format
    time_t procstart = (time_t) (time(NULL) - uptime_seconds \
                                 + (double)start_time_jiffies / clk_tck);
    struct tm *timeinfo = localtime(&procstart);
    char time_str[20];
    strftime(time_str, sizeof(time_str), "%b%d_%H%M%S", timeinfo);
    // generate TRACE_DIR and create if need
    char* TRACE_DIR = (char*) malloc(strlen(NEUTRINO_TRACEDIR) + 30);
    sprintf(TRACE_DIR, "%s/%s_%d", NEUTRINO_TRACEDIR, time_str, getpid());
    // get or create the TRACE_DIR
    if (access(TRACE_DIR, F_OK) != 0) { 
        if (mkdir(TRACE_DIR, 0755) != 0) {
            perror("Can not create TRACE_DIR");
            exit(EXIT_FAILURE);
        }
    }
    return TRACE_DIR;
}

/**
 * @note semaphores for thread safety: Neutrino don't envision multi-threading
 *       but upper layer, like PyTorch may use multi-threading for their need
 * There's only a few critical section like init and hashmaps
 */
static pthread_once_t mutex_is_initialized = PTHREAD_ONCE_INIT; // for safe initialization of mutex
static pthread_mutex_t mutex; // initialization is protected by the mutex_is_initialized
void mutex_init(void) { pthread_mutex_init(&mutex, NULL); }

/**
 * initialize event_log, dir, envvar, these kind of platform-diagnostic commons
 * need to be called at the beginning of platform-specific init()
 * @note shall be executed with mutex protection!!!
 */
static void common_init(void) {
    // first verify NEUTRINO_PROBE is set 
    char* NEUTRINO_PROBES = getenv("NEUTRINO_PROBES");
    if (NEUTRINO_PROBES == NULL) {
        fprintf(stderr, "[error] envariable NEUTRINO_PROBES not set\n");
    }
    // get environment variables
    NEUTRINO_REAL_DRIVER = getenv("NEUTRINO_REAL_DRIVER");
    if (NEUTRINO_REAL_DRIVER == NULL) {
        fprintf(stderr, "[error] envariable NEUTRINO_REAL_DRIVER not set\n");
        exit(EXIT_FAILURE);
    }
    NEUTRINO_PYTHON = getenv("NEUTRINO_PYTHON");
    if (NEUTRINO_PYTHON == NULL) {
        fprintf(stderr, "[error] envariable NEUTRINO_PYTHON not set\n");
        exit(EXIT_FAILURE);
    }
    NEUTRINO_PROBING_PY = getenv("NEUTRINO_PROBING_PY");
    if (NEUTRINO_PROBING_PY == NULL) {
        fprintf(stderr, "[error] envariable NEUTRINO_PROBING_PY not set\n");
        exit(EXIT_FAILURE);
    }
    NEUTRINO_CALLBACK = getenv("NEUTRINO_CALLBACK");
    // External Feature Controls
    char* dynamic = getenv("NEUTRINO_DYNAMIC");
    if (dynamic != NULL && atoi(dynamic) != 0) {
        DYNAMIC = 1;
    }
    char* verbose = getenv("NEUTRINO_VERBOSE");
    if (verbose != NULL && atoi(verbose) != 0) { // otherwise, default is 0
        VERBOSE = 1;
    } 
    char* benchmark = getenv("NEUTRINO_BENCHMARK");
    if (benchmark != NULL && atoi(benchmark) != 0) {
        NEUTRINO_BENCHMARK = 1;
    }
    char* memusage = getenv("NEUTRINO_MEMUSAGE");
    if (memusage != NULL && atoi(memusage) != 0) {
        NEUTRINO_MEMUSAGE = 1;
    }
    // generate TRACE_DIR and create if need
    char* TRACE_DIR = get_tracedir();
    fprintf(stderr, "[info] trace in %s \n", TRACE_DIR);
    // RESULT_DIR put metrics
    RESULT_DIR = malloc(strlen(TRACE_DIR) + 8);
    sprintf(RESULT_DIR, "%s/result", TRACE_DIR);
    if (mkdir(RESULT_DIR, 0755) != 0) {
        perror("Can not create RESULT_DIR");
        exit(EXIT_FAILURE);
    }
    // KERNEL_DIR is workdirs of the probe engine
    KERNEL_DIR = malloc(strlen(TRACE_DIR) + 8);
    sprintf(KERNEL_DIR, "%s/kernel", TRACE_DIR);
    if (mkdir(KERNEL_DIR, 0755) != 0) {
        perror("Can not create KERNEL_DIR");
        exit(EXIT_FAILURE);
    }
    /**
     * Dump the probe.toml to TRACE_DIR/probe.toml
     */
    char* TMP_PATH = malloc(strlen(TRACE_DIR) + 20);
    sprintf(TMP_PATH, "%s/probe.toml", TRACE_DIR);
    FILE* probes_f = fopen(TMP_PATH, "w");
    if (probes_f == NULL) {
        perror("Can open probe.toml");
        exit(EXIT_FAILURE);
    }
    fwrite(NEUTRINO_PROBES, sizeof(char), strlen(NEUTRINO_PROBES), probes_f);
    fclose(probes_f);
    /**
     * Dump the trace reading code to the TRACE_DIR/read.py
     */
    const char* NEUTRINO_READING = getenv("NEUTRINO_READING");
    if (NEUTRINO_READING) {
        sprintf(TMP_PATH, "%s/read.py", TRACE_DIR);
        FILE* reading_f = fopen(TMP_PATH, "w");
        if (reading_f != NULL) {
            fwrite(NEUTRINO_READING, sizeof(char), strlen(NEUTRINO_READING), reading_f);
            fclose(reading_f);
        }
    }
    /**
     * Open the event.log as the operation log of hook driver
     */
    sprintf(TMP_PATH, "%s/event.log", TRACE_DIR);
    event_log = fopen(TMP_PATH, "a");
    if (event_log == NULL) {
        perror("Can open event.log");
        exit(EXIT_FAILURE);
    }
    // print metadata like pid and cmdline
    fprintf(event_log, "[init] pid %d\n", getpid()); // print the process id
    // get command line arguments
    char cmdpath[128], cmdline[1024];
    sprintf(cmdpath, "/proc/%d/cmdline", getpid());
    FILE *cmdfile = fopen(cmdpath, "r");
    size_t len = fread(cmdline, 1, sizeof(cmdline) - 1, cmdfile);
    if (len > 0) {
        // Replace null characters with spaces
        for (int i = 0; i < len; i++) {
            if (cmdline[i] == '\0') { 
                cmdline[i] = ' ';
            }
        }
    }
    fclose(cmdfile);
    // print the command line, helpful to correlate source code
    fprintf(event_log, "[init] cmd %zu %s\n", len, cmdline); 
    fflush(event_log);
    // load real driver shared library
    shared_lib = dlopen(NEUTRINO_REAL_DRIVER, RTLD_LAZY);
    CHECK_DL();
    fprintf(event_log, "[init] dl %p\n", shared_lib); 
    fflush(event_log);
    // get the starting time
    clock_gettime(CLOCK_REALTIME, &start);
    free(TMP_PATH);
    free(TRACE_DIR);
    // don't free RESULT_DIR and KERNEL_DIR, we will use it later
}

/**
 * Neutrino Trace Headers being dumped
 * 
 * Similar to most binary, Neutrino trace started with a header (trace_header_t) and
 * followed by an array of section (trace_section_t) for each probe, and datas.
 * @todo add section table similar to ELF for faster parsing
 * @todo add a placeholder for probe type
 * @todo standardize saving from cuda.c/hip.c
 */
typedef struct {
    // basic launch configuration
    uint32_t gridDimX;
    uint32_t gridDimY;
    uint32_t gridDimZ;
    uint32_t blockDimX;
    uint32_t blockDimY;
    uint32_t blockDimZ;
    uint32_t sharedMemBytes; // @todo replace with WARP_SIZE
    // all above from CUDA/ROCm launch configuration
    uint32_t numProbes; // number of traces exposed
    // followed by an array of trace_section_t
} trace_header_t;

typedef struct {
    uint32_t size;    // size of record per thread/warp in byte
    uint32_t warpDiv; // warpSize for warp-level, 1 for thread-level
    uint64_t offset;  // offset for fseek
} trace_section_t;

/**
 * GPU Code Binary Header Definitions, supporting cubin, fatbin, text(ptx/gcn asm)
 * @note ELF is standard ELF and fatbin 
 * @todo support .hsaco 
 */

// fat binary header defined for fatbin
// @cite https://github.com/rvbelapure/gpu-virtmem/blob/master/cudaFatBinary.h
typedef struct {
    unsigned int           magic;   // magic numbers, checked it before
    unsigned int           version; // fatbin version
    unsigned long long int size;    // fatbin size excluding 
} fatBinaryHeader;

// the fat binary wrapper header
// @see fatbinary_section.h in cuda toolkit
typedef struct {
    int magic;
    int version;
    unsigned long long* data;  // pointer to real fatbin
    void *filename_or_fatbin;  /* version 1: offline filename,
                                * version 2: array of prelinked fatoutbuf */
} fatBinaryWrapper;

/**
 * Binary Size Calculation based on header because code are of void*
 * @note Please use unified API get_managed_code_size
 */

#define ELF 1
#define FATBIN 2
#define WRAPPED_FATBIN 3
#define PTX 4
#define ERROR_TYPE 0

static const char *code_types[] = { "error", "elf", "fatbin", "warpped_fatbin", "ptx" };

// check if content of void *ptr is ELF format or FatBinary Format
static int check_magic(const int magic) {
    if (magic == 0x464c457f || magic == 0x7f454c46) {
        return ELF;
    } else if (magic == 0xba55ed50 || magic == 0x50ed55ba) {
        return FATBIN;
    } else if (magic == 0x466243B1 || magic == 0xB1436246) {
        return WRAPPED_FATBIN;
    } else {
        return ERROR_TYPE;
    }
}

static unsigned long long get_elf_size(const Elf64_Ehdr *header) {    
    // for standard executable, use section header
    size_t size = header->e_shoff + header->e_shentsize * header->e_shnum;

    // for cubin, only program header can give correct size
    if (header->e_phoff + header->e_phentsize * header->e_phnum > size)
        size = header->e_phoff + header->e_phentsize * header->e_phnum;

    return size;
}

static unsigned long long get_fatbin_size(const fatBinaryHeader *header) {
    // size of fatbin is given by header->size and don't forget sizeof header
    return header->size + sizeof(fatBinaryHeader); 
}

static int get_managed_code_size(void** managed, size_t* size, const void* bin) {
    int magic, bin_type;
    // check the magic number for binary type
    memcpy(&magic, bin, sizeof(int)); 
    bin_type = check_magic(magic);
    const void *code;
    if (bin_type == WRAPPED_FATBIN) { 
        fatBinaryWrapper wrapper;
        memcpy(&wrapper, bin, sizeof(wrapper));
        fatBinaryHeader header;
        memcpy(&header, wrapper.data, sizeof(header));
        *size = get_fatbin_size(&header);
        code = (const void*) wrapper.data;
        fprintf(event_log, "[bin] type %s size %zu\n", code_types[bin_type], *size);
    } else if (bin_type == FATBIN) { 
        fatBinaryHeader header;
        memcpy(&header, bin, sizeof(header));
        *size = get_fatbin_size(&header);
        code = (const void*) bin;
        fprintf(event_log, "[bin] type %s size %zu\n", code_types[bin_type], *size);
    } else if (bin_type == ELF) {
        Elf64_Ehdr header;
        memcpy(&header, bin, sizeof(header));
        *size = get_elf_size(&header);
        code = (const void*) bin;
        fprintf(event_log, "[bin] type %s size %zu\n", code_types[bin_type], *size);
    } else if (bin_type == ERROR_TYPE) {
        // check whether it's text file of NULL-Terminated ASM File
        // ptx must start with '//' and end with '\0'
        // @todo add GCN ASM here
        const char* ptx = (const char*) bin;
        if (ptx[0] == '/' && ptx[1] == '/') {
            *size = strlen(ptx); // naturally count till '\0'
            code = (const void*) bin;
            bin_type = PTX;
            fprintf(event_log, "[bin] type %s size %zu\n", code_types[bin_type], *size);
        } else { // still unrecognize, report the bug and terminates
            fprintf(event_log, "[bin] unrecognize %d\n", magic);
            return -1;
        }
    }
    // copy the image to a new managed and protected place
    *managed = malloc(*size);
    memcpy(*managed, code, *size);
    return 0;
}

/**
 * Hash map (uthash) as Code Cache to avoid re-probing the same GPU function, include:
 * 1. Binary Map for GPU code before probe, could be library, module, function
 * 2. Function Map for probed code, including original/pruned/probed function
 * @todo binmap logics are duplicated (update_key, update_name_key), simplify them
 */

typedef struct {
    void* key;  // could be CUlibrary, CUmodule, CUfunction or HIP equivalent
    void* code; // the binary code
    char* name; // name of function
    unsigned long long size; // size of bin
    UT_hash_handle hh; 
} binmap_item;

static binmap_item*  binmap  = NULL; // UTHash Initialization

// add item to bin hashmap, won't raise
int binmap_set(void* key, void* code, unsigned long long size, char* name) {
    pthread_mutex_lock(&mutex);
    binmap_item* item = (binmap_item*) malloc(sizeof(binmap_item));
    item->key = key;
    item->code = code;
    item->size = size;
    item->name = name;
    HASH_ADD_PTR(binmap, key, item);
    pthread_mutex_unlock(&mutex);
    return 0;
}

int binmap_update_key(void* old_key, void* new_key) {
    pthread_mutex_lock(&mutex);
    binmap_item* item;
    HASH_FIND_PTR(binmap, &old_key, item);
    if (item != NULL) {
        HASH_DEL(binmap, item);
        item->key = new_key;
        HASH_ADD_PTR(binmap, key, item);
        pthread_mutex_unlock(&mutex);
        return 0;
    } else {
        pthread_mutex_unlock(&mutex);
        return -1;
    }
}

/**
 * Update both the name and the key, favored by cuModuleGetFunction
 * and cuLibraryGetKernel, which will create new entry to hold the
 * new key and value, but underlying binary and size will be shared
 */
int binmap_update_name_key(void* old_key, void* new_key, char* name) {
    pthread_mutex_lock(&mutex);
    binmap_item* old_item;
    HASH_FIND_PTR(binmap, &old_key, old_item);
    if (old_item != NULL) { 
        binmap_item* new_item = (binmap_item*) malloc(sizeof(binmap_item));
        new_item->name = name;
        new_item->key  = new_key;
        new_item->size = old_item->size;
        new_item->code  = old_item->code;
        HASH_ADD_PTR(binmap, key, new_item);
        pthread_mutex_unlock(&mutex);
        return 0;
    } else {
        pthread_mutex_unlock(&mutex);
        return -1;
    }
}

int binmap_get(void* key, size_t* size, char** name, void** code) {
    pthread_mutex_lock(&mutex);
    binmap_item* item;
    HASH_FIND_PTR(binmap, &key, item);
    if (item != NULL) { 
        *size = item->size;
        *name = item->name;
        *code = item->code;
        pthread_mutex_unlock(&mutex);
        return 0;
    } else {
        pthread_mutex_unlock(&mutex);
        return -1;
    }
}

// function map items, used as JIT code cache to avoid re-compilation
typedef struct {
    void* original;    // original CUfunction/HIPfunction
    char* name;        // name of function, if made possible, can be NULL
    int n_param;       // number of parameters, obtained from parsing
    int n_probe;       // number of probes that would dump memory
    int* probe_sizes;  // sizes of probe memory, order matches
    int* probe_types;  // types of probe, 
    bool succeed;      // specify JIT status -> if failed, always goto backup
    void* probed;      // probed CUfunction/HIPfunction
    void* pruned;      // pruned CUfunction/HIPfunction, for benchmark only
    void* countd;      // counting CUfunction/HIPfunction, for DYNAMIC=TRUE only
    UT_hash_handle hh; // reserved by uthash
} funcmap_item_t;

static funcmap_item_t* funcmap = NULL;

// add an item to the hashmap-based code cache
int funcmap_set(void* original, char* name, int n_param, int n_probe, int* probe_sizes, int* probe_types, bool succeed, void* probed, void* pruned, void* countd) {
    pthread_mutex_lock(&mutex);
    funcmap_item_t* item = (funcmap_item_t*) malloc(sizeof(funcmap_item_t));
    item->original = original;
    item->probed = probed;
    item->pruned = pruned;
    item->countd = countd;
    item->name = name;
    item->n_param = n_param;
    item->n_probe = n_probe;
    item->probe_sizes = probe_sizes;
    item->probe_types = probe_types;
    item->succeed = succeed; // add func status -> if failed then no need to try probing again and again
    HASH_ADD_PTR(funcmap, original, item);
    pthread_mutex_unlock(&mutex);
    return 0;
}

// get an item from hashmap-based code cache
int funcmap_get(void* original, char** name, int* n_param, int* n_probe, int** probe_sizes, int** probe_types, bool* succeed, void** probed, void** pruned, void** countd) {
    pthread_mutex_lock(&mutex);
    funcmap_item_t* item;
    HASH_FIND_PTR(funcmap, &original, item);
    if (item != NULL) {
        *name        = item->name;
        *n_param     = item->n_param;
        *n_probe     = item->n_probe;
        *probe_sizes = item->probe_sizes;
        *probe_types = item->probe_types;
        *succeed     = item->succeed;
        *probed      = item->probed;
        *pruned      = item->pruned;
        *countd      = item->countd;
        pthread_mutex_unlock(&mutex);
        return 0;
    } else { 
        pthread_mutex_unlock(&mutex);
        return -1;
    }        
}

/**
 * hash text based on sha1 algorithm, mainly to flush kernel name, because the
 * C++ template can be long and contains weird bytes (to ASCII).
 * @note not memory safe, remember to free pointer returned
 */
char* sha1(const char* text) {
	SHA1_CTX ctx;
	sha1_init(&ctx);
	sha1_update(&ctx, text, strlen(text));
	BYTE hash[SHA1_BLOCK_SIZE];
	sha1_final(&ctx, hash);
	char* hexed = malloc(41 * sizeof(char)); // 1 for '\0'
	sprintf(hexed, "%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x",
		hash[0], hash[1], hash[2], hash[3], hash[4], hash[5], hash[6], hash[7], hash[8], hash[9],
		hash[10],hash[11],hash[12],hash[13],hash[14],hash[15],hash[16],hash[17],hash[18],hash[19]);
	return hexed;
}

/**
 * File Utilities, Read File without knowing size
 * @note remember to free pointer returned
 */
inline void* readf(char* path, const char* mode) {
    FILE* file = fopen(path, mode);
    fseek(file, 0, SEEK_END);
    long file_size = ftell(file);
    fseek(file, 0, SEEK_SET);
    void* ptr = malloc(file_size);
    size_t read_size = fread(ptr, 1, file_size, file);
    if (read_size != file_size)
        fprintf(stderr, "read size mismatched\n");
    fclose(file);
    return ptr;
}

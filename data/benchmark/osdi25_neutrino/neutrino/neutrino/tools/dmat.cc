/**
 * A Faster C++ STL Based Sparsifying
 */

#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unordered_map>
#include <map>
#include <unordered_set>
#include <vector>
 
// Mask out last 16bit of 64bit address -> 16MB Page Size
#define PAGE_MASK 0xFFFFFFFFFFFF0000

typedef struct {
    // basic launch configuration
    uint32_t gridDimX;
    uint32_t gridDimY;
    uint32_t gridDimZ;
    uint32_t blockDimX;
    uint32_t blockDimY;
    uint32_t blockDimZ;
    uint32_t sharedMemBytes; 
    // all above from CUDA/ROCm launch configuration
    uint32_t numProbes; // number of traces exposed
    // followed by an array of trace_section_t
} trace_header_t;

// @todo add a placeholder for probe level, aka warp/thread
typedef struct {
    uint32_t size;    // size of record per thread/warp in byte
    uint32_t warpDiv; // warpSize for warp-level, 1 for thread-level
    uint64_t offset;  // offset for fseek
} trace_section_t;

typedef struct {
    uint64_t clock;
    uint64_t addrs;
} dmat_t;

int main(int argc, char* argv[]) {
    if (argc < 3) {
    fprintf(stderr, "Usage: %s <input> <output>\n", argv[0]);
        return EXIT_FAILURE;
    }
    
    FILE* inputf = fopen(argv[1], "rb");
    if (inputf == NULL) {
        fprintf(stderr, "can't open input %s \n", argv[1]);
        return EXIT_FAILURE;
    }

    FILE* outf = fopen(argv[2], "wb");
    if (outf == NULL) {
        fprintf(stderr, "can't open output %s \n", argv[2]);
        return EXIT_FAILURE;
    }

    trace_header_t header;
    size_t elements_read;
    elements_read = fread(&header, sizeof(header), 1, inputf);
    
    uint32_t gridSize = header.gridDimX * header.gridDimY * header.gridDimZ;
    uint32_t blockSize = header.blockDimX * header.blockDimY * header.blockDimZ;

    trace_section_t section;
    elements_read = fread(&section, sizeof(section), 1, inputf);

    uint64_t size = section.size;
    uint64_t offset = section.offset;

    fprintf(stderr, "[info] size: %lu, gridSize: %u, blockSize: %u, offset %lu, numProbes: %u\n", size, gridSize, blockSize, offset, header.numProbes);

    // use fseek to locate the section starting point 
    fseek(inputf, offset, SEEK_SET);

    // allocate buffer size to contain the record, here we know it's uint64_t
    void* content = (void*) malloc(size * gridSize * blockSize);
    elements_read = fread(content, size * gridSize * blockSize, 1, inputf);
    
    // I am SORRY I have to use C++ Standard Template Library Containers
    // page_reference_map := time -> page -> count
    std::unordered_map<uint64_t, std::map<uint64_t, uint32_t>> page_reference_map;
    std::unordered_set<uint64_t> pages; 

    uint64_t max_clock = 0;
    for (int blockIdx = 0; blockIdx < gridSize; blockIdx++) {
        for (int threadIdx = 0; threadIdx < blockSize; threadIdx++) {
            // Here we know every record takes 16 bytes
            for (int recordIdx = 0; recordIdx < (size / 16); recordIdx++) {
                dmat_t record = *(dmat_t*)(content);
                if (record.clock != ~0) { // valid record
                    max_clock = (record.clock > max_clock) ? record.clock : max_clock;
                    uint64_t page = record.addrs & PAGE_MASK;
                    page_reference_map[record.clock][page]++; // accumulate the offset
                    pages.insert(page);
                }
                content += sizeof(dmat_t); // anyway offset by 16 bytes
            }
        }
    }
    
    // now let's dump it to disk
    size_t num_clocks = page_reference_map.size(), num_pages = pages.size();

    fprintf(stderr, "\n[info] num_pages: %lu, num_clocks: %lu, max_clock: %lu\n", num_pages, num_clocks, max_clock);
    
    fwrite(&num_pages, sizeof(num_pages), 1, outf);
    fwrite(&num_clocks, sizeof(num_clocks), 1, outf);

    std::vector<uint64_t> page_vec(pages.begin(), pages.end()); // set -> vector
    fwrite(page_vec.data(), sizeof(uint64_t), num_pages, outf);

    for (const auto& [clock, pages_clock] : page_reference_map) {
        fwrite(&clock, sizeof(clock), 1, outf);
        uint64_t size = pages_clock.size();
        fwrite(&size,  sizeof(size),  1, outf);
        for (const auto& [page, count] : pages_clock) {
            fwrite(&page,  sizeof(page),  1, outf);
            fwrite(&count, sizeof(count), 1, outf);
        }
    }
    fclose(outf);
    return EXIT_SUCCESS;
}
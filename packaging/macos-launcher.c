#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char self[PATH_MAX], target[PATH_MAX];
    uint32_t size = sizeof(self);
    if (_NSGetExecutablePath(self, &size) != 0) return 1;
    char *end = strrchr(self, '/');
    if (!end) return 1;
    *end = '\0';
#if defined(__arm64__)
    const char *arch = "arm64";
#elif defined(__x86_64__)
    const char *arch = "x86_64";
#else
#error Unsupported architecture
#endif
    if (snprintf(target, sizeof(target), "%s/../Resources/runtime-%s/FinancialFeeSystem", self, arch) >= sizeof(target))
        return 1;
    argv[0] = target;
    execv(target, argv);
    perror("Cannot start Financial Fee System");
    return 1;
}

// C headers the Swift sources under approve/ need: OpenPAM for the module and libproc for
// reading the calling sudo process's working directory and parent name.
#include <security/pam_appl.h>
#include <security/pam_modules.h>
#include <security/openpam.h>
#include <libproc.h>
#include <sys/sysctl.h>

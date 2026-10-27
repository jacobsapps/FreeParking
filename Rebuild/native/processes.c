// Read-only macOS process inspection. No signals, commands, or terminal writes.
#include <libproc.h>
#include <sys/proc_info.h>
#include <sys/sysctl.h>
#include <sys/stat.h>
#include <arpa/inet.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <CoreGraphics/CoreGraphics.h>

double fp_main_screen_height(void) {
    return CGDisplayBounds(CGMainDisplayID()).size.height;
}

struct fp_process {
    int32_t pid, ppid;
    uint64_t seconds, micros;
    char tty[128], executable[4096];
};
struct fp_file { int32_t kind; char value[4096]; };

int fp_pids(int32_t *out, int capacity) {
    int bytes = proc_listpids(PROC_ALL_PIDS, 0, out, capacity * (int)sizeof(*out));
    if (bytes <= 0) return -(errno ? errno : EIO);
    if (bytes >= capacity * (int)sizeof(*out)) return -ENOSPC;
    return bytes / (int)sizeof(*out);
}
int fp_process(int32_t pid, struct fp_process *out) {
    struct proc_bsdinfo info = {0};
    memset(out, 0, sizeof(*out));
    if (proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info)) {
        // SIP restricts libproc for some system daemons; the public process
        // table still exposes their TTY/name, just as ps does. Never drop them.
        struct kinfo_proc kinfo = {0}; size_t size = sizeof(kinfo);
        int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, pid};
        if (sysctl(mib, 4, &kinfo, &size, NULL, 0) != 0) return -errno;
        if (size != sizeof(kinfo)) return -ESRCH;
        info.pbi_pid = kinfo.kp_proc.p_pid;
        info.pbi_ppid = kinfo.kp_eproc.e_ppid;
        info.e_tdev = kinfo.kp_eproc.e_tdev;
        info.pbi_start_tvsec = kinfo.kp_proc.p_starttime.tv_sec;
        info.pbi_start_tvusec = kinfo.kp_proc.p_starttime.tv_usec;
        snprintf(info.pbi_name, sizeof(info.pbi_name), "%s", kinfo.kp_proc.p_comm);
    }
    out->pid = info.pbi_pid; out->ppid = info.pbi_ppid;
    out->seconds = info.pbi_start_tvsec; out->micros = info.pbi_start_tvusec;
    if (info.e_tdev == (uint32_t)-1) snprintf(out->tty, sizeof(out->tty), "/dev/??");
    else {
        const char *name = devname((dev_t)info.e_tdev, S_IFCHR);
        if (!name) return -EIO;
        snprintf(out->tty, sizeof(out->tty), "/dev/%s", name);
    }
    if (proc_pidpath(pid, out->executable, sizeof(out->executable)) <= 0) {
        // Keep even inaccessible jobs in the inventory. Their kernel name
        // makes an attached unknown job block parking instead of disappearing.
        snprintf(out->executable, sizeof(out->executable), "%s",
                 info.pbi_name[0] ? info.pbi_name : info.pbi_comm);
    }
    return 0;
}
int fp_cwd(int32_t pid, char *out, int capacity) {
    struct proc_vnodepathinfo info = {0};
    if (proc_pidinfo(pid, PROC_PIDVNODEPATHINFO, 0, &info, sizeof(info)) != sizeof(info))
        return -(errno ? errno : EIO);
    size_t size = strnlen(info.pvi_cdir.vip_path, sizeof(info.pvi_cdir.vip_path));
    if (!size || size >= (size_t)capacity) return -ENOSPC;
    memcpy(out, info.pvi_cdir.vip_path, size + 1);
    return 0;
}
int fp_files(int32_t pid, struct fp_file *out, int capacity, int sockets_only) {
    errno = 0;
    int bytes = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, NULL, 0);
    if (bytes < 0) return -(errno ? errno : EIO);
    if (!bytes) return errno ? -errno : 0;
    int allocated = bytes + 256 * sizeof(struct proc_fdinfo);
    struct proc_fdinfo *fds = malloc(allocated);
    if (!fds) return -ENOMEM;
    bytes = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, fds, allocated);
    if (bytes <= 0 || bytes >= allocated) { free(fds); return -(errno ? errno : ENOSPC); }
    int used = 0;
    for (int i = 0; i < bytes / (int)sizeof(*fds); i++) {
        struct fp_file item = {0}; int result = 0;
        errno = 0;
        if (fds[i].proc_fdtype == PROX_FDTYPE_VNODE && !sockets_only) {
            struct vnode_fdinfowithpath info = {0};
            result = proc_pidfdinfo(pid, fds[i].proc_fd, PROC_PIDFDVNODEPATHINFO, &info, sizeof(info));
            if (result == sizeof(info) && info.pvip.vip_path[0]) {
                item.kind = 1;
                snprintf(item.value, sizeof(item.value), "%s", info.pvip.vip_path);
            }
        } else if (fds[i].proc_fdtype == PROX_FDTYPE_SOCKET) {
            struct socket_fdinfo info = {0};
            result = proc_pidfdinfo(pid, fds[i].proc_fd, PROC_PIDFDSOCKETINFO, &info, sizeof(info));
            struct tcp_sockinfo *tcp = &info.psi.soi_proto.pri_tcp;
            if (result == sizeof(info) && info.psi.soi_kind == SOCKINFO_TCP &&
                tcp->tcpsi_state == TSI_S_LISTEN && (tcp->tcpsi_ini.insi_vflag & INI_IPV4) &&
                ntohl(tcp->tcpsi_ini.insi_laddr.ina_46.i46a_addr4.s_addr) == INADDR_LOOPBACK) {
                item.kind = 2;
                snprintf(item.value, sizeof(item.value), "http://127.0.0.1:%u", ntohs(tcp->tcpsi_ini.insi_lport));
            }
        } else continue;
        if (result <= 0 && errno != EBADF && errno != ENOENT && errno != ESRCH) {
            int error = errno ? errno : EIO; free(fds); return -error;
        }
        if (item.kind) {
            if (used == capacity) { free(fds); return -ENOSPC; }
            out[used++] = item;
        }
    }
    free(fds); return used;
}
int fp_argv(int32_t pid, char *out, int capacity) {
    int mib[3] = {CTL_KERN, KERN_PROCARGS2, pid}; size_t size = capacity;
    if (sysctl(mib, 3, out, &size, NULL, 0) != 0) return -errno;
    if (size >= (size_t)capacity) return -ENOSPC;
    return (int)size;
}

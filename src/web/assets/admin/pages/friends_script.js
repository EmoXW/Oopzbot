    const friendLimit = 20;
    let friendOffset = 0, requestOffset = 0, friendSearch = "";
    let friendGeneration = 0, requestGeneration = 0;
    let remarkUid = "", remarkGeneration = 0, remarkSaving = false;
    let remarkReturnFocus = null;
    const responding = new Set();
    const friendEsc = (value) => AdminShell.escapeHtml(String(value ?? ""));

    function friendState(text, kind) {
      ["topStatus", "mobileStatus", "friendsState"].forEach(id => AdminShell.setStatus(text, kind, id));
    }

    function friendIdentity(item) {
      return '<strong>' + friendEsc(item.name || item.uid) + '</strong><div class="friends-copy" style="overflow-wrap:anywhere">' + friendEsc(item.uid) + '</div>';
    }

    function friendDate(value) {
      if (!value) return "—";
      let parsed = value;
      if (/^\d+$/.test(String(value))) {
        parsed = Number(value);
        if (parsed > 0 && parsed < 100000000000) parsed *= 1000;
      }
      const date = new Date(parsed);
      return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
    }

    function friendPagination(prefix, offset, total) {
      AdminShell.byId(prefix + "Prev").disabled = offset === 0;
      AdminShell.byId(prefix + "Next").disabled = offset + friendLimit >= total;
      AdminShell.byId(prefix + "Page").textContent = '第 ' + (Math.floor(offset / friendLimit) + 1) + ' / ' + Math.max(1, Math.ceil(total / friendLimit)) + ' 页，共 ' + total + ' 条';
    }

    async function loadFriendList() {
      const generation = ++friendGeneration;
      try {
        const params = new URLSearchParams({ search: friendSearch, offset: String(friendOffset), limit: String(friendLimit) });
        const data = await AdminShell.req('/admin/api/friends?' + params);
        if (generation !== friendGeneration) return;
        if (friendOffset && friendOffset >= data.total) { friendOffset = Math.max(0, Math.floor((data.total - 1) / friendLimit) * friendLimit); return loadFriendList(); }
        AdminShell.byId("friendsTotal").textContent = String(data.total_count);
        AdminShell.byId("friendsOnline").textContent = String(data.online_count);
        AdminShell.byId("friendRows").innerHTML = data.friends.length ? data.friends.map(item => '<tr><td>' + friendIdentity(item) + '</td><td><span class="status-text ' + (item.online ? 'is-success' : '') + '">' + (item.online ? '在线' : '离线') + '</span></td><td><button class="btn btn-ghost" type="button" data-action="edit-friend-remark" data-uid="' + friendEsc(item.uid) + '" data-name="' + friendEsc(item.name) + '">编辑备注</button></td></tr>').join('') : '<tr><td colspan="3" class="empty-state">' + (friendSearch ? '没有找到匹配的好友' : '当前账号暂无好友') + '</td></tr>';
        friendPagination("friend", friendOffset, data.total);
        return true;
      } catch (error) {
        if (generation !== friendGeneration) return;
        AdminShell.byId("friendRows").innerHTML = '<tr><td colspan="3" class="empty-state">' + friendEsc(error.message) + '</td></tr>';
        AdminShell.byId("friendPrev").disabled = AdminShell.byId("friendNext").disabled = true;
        return false;
      }
    }

    async function loadFriendRequests() {
      const generation = ++requestGeneration;
      try {
        const data = await AdminShell.req('/admin/api/friends/requests?offset=' + requestOffset + '&limit=' + friendLimit);
        if (generation !== requestGeneration) return;
        if (requestOffset && requestOffset >= data.total) { requestOffset = Math.max(0, Math.floor((data.total - 1) / friendLimit) * friendLimit); return loadFriendRequests(); }
        AdminShell.byId("requestsTotal").textContent = String(data.total);
        AdminShell.byId("requestRows").innerHTML = data.requests.length ? data.requests.map(item => {
          const disabled = responding.has(item.request_id) ? ' disabled' : '';
          const attrs = ' data-uid="' + friendEsc(item.uid) + '" data-id="' + friendEsc(item.request_id) + '" data-name="' + friendEsc(item.name) + '"';
          return '<tr><td>' + friendIdentity(item) + '</td><td>' + friendEsc(friendDate(item.created_at)) + '</td><td><div class="action-row"><button class="btn btn-primary" type="button" data-action="accept-friend-request"' + attrs + disabled + '>同意</button><button class="btn btn-danger" type="button" data-action="reject-friend-request"' + attrs + disabled + '>拒绝</button></div></td></tr>';
        }).join('') : '<tr><td colspan="3" class="empty-state">暂无待处理的好友申请</td></tr>';
        friendPagination("request", requestOffset, data.total);
        return true;
      } catch (error) {
        if (generation !== requestGeneration) return;
        AdminShell.byId("requestRows").innerHTML = '<tr><td colspan="3" class="empty-state">' + friendEsc(error.message) + '</td></tr>';
        AdminShell.byId("requestPrev").disabled = AdminShell.byId("requestNext").disabled = true;
        return false;
      }
    }

    async function refreshFriends() {
      friendState("正在同步", "warning");
      const results = await Promise.all([loadFriendList(), loadFriendRequests()]);
      friendState(results.every(Boolean) ? "已同步" : "部分数据加载失败", results.every(Boolean) ? "success" : "error");
    }

    async function respondFriendRequest(element, agree) {
      const id = element.dataset.id;
      if (responding.has(id)) return;
      const action = agree ? "同意" : "拒绝";
      if (!confirm(action + '「' + (element.dataset.name || element.dataset.uid) + '」的好友申请？')) return;
      responding.add(id);
      document.querySelectorAll('#requestRows button').forEach(button => { if (button.dataset.id === id) button.disabled = true; });
      try {
        const result = await AdminShell.req('/admin/api/friends/requests/respond', { method: 'POST', body: JSON.stringify({ uid: element.dataset.uid, request_id: id, agree: agree }) });
        AdminShell.showMessage("friendsMsg", result.message);
      } catch (error) {
        AdminShell.showMessage("friendsMsg", error.message, true);
      } finally {
        responding.delete(id);
        await refreshFriends();
      }
    }

    async function editFriendRemark(element) {
      if (remarkSaving) return;
      const generation = ++remarkGeneration;
      remarkReturnFocus = element;
      remarkUid = element.dataset.uid;
      AdminShell.byId("friendRemarkUser").textContent = (element.dataset.name || remarkUid) + ' · ' + remarkUid;
      AdminShell.byId("friendRemarkInput").value = '';
      AdminShell.byId("friendRemarkInput").disabled = true;
      AdminShell.byId("friendRemarkSave").disabled = true;
      AdminShell.showMessage("friendRemarkMsg", "正在读取备注…");
      AdminShell.byId("friendRemarkOverlay").classList.add("is-open");
      AdminShell.byId("friendRemarkDialog").classList.add("is-open");
      AdminShell.byId("friendRemarkDialog").inert = false;
      AdminShell.byId("friendRemarkDialog").setAttribute("aria-hidden", "false");
      try {
        const data = await AdminShell.req('/admin/api/friends/' + encodeURIComponent(remarkUid) + '/remark');
        if (generation !== remarkGeneration) return;
        AdminShell.byId("friendRemarkInput").value = data.remark || '';
        AdminShell.byId("friendRemarkInput").disabled = false;
        AdminShell.byId("friendRemarkSave").disabled = false;
        AdminShell.showMessage("friendRemarkMsg", "");
        AdminShell.byId("friendRemarkInput").focus();
      } catch (error) {
        if (generation === remarkGeneration) AdminShell.showMessage("friendRemarkMsg", error.message, true);
      }
    }

    function closeFriendRemark() {
      if (remarkSaving) return;
      remarkGeneration++;
      remarkUid = '';
      if (remarkReturnFocus && remarkReturnFocus.isConnected) remarkReturnFocus.focus();
      AdminShell.byId("friendRemarkOverlay").classList.remove("is-open");
      AdminShell.byId("friendRemarkDialog").classList.remove("is-open");
      AdminShell.byId("friendRemarkDialog").inert = true;
      AdminShell.byId("friendRemarkDialog").setAttribute("aria-hidden", "true");
    }

    async function saveFriendRemark() {
      if (!remarkUid || remarkSaving) return;
      remarkSaving = true;
      AdminShell.byId("friendRemarkSave").disabled = true;
      try {
        const result = await AdminShell.req('/admin/api/friends/' + encodeURIComponent(remarkUid) + '/remark', { method: 'POST', body: JSON.stringify({ remark: AdminShell.byId("friendRemarkInput").value }) });
        remarkSaving = false;
        closeFriendRemark();
        AdminShell.showMessage("friendsMsg", result.message);
        await loadFriendList();
      } catch (error) {
        AdminShell.showMessage("friendRemarkMsg", error.message, true);
      } finally {
        remarkSaving = false;
        AdminShell.byId("friendRemarkSave").disabled = false;
      }
    }

    function searchFriends() {
      friendSearch = AdminShell.byId("friendSearch").value.trim(); friendOffset = 0; loadFriendList();
    }

    AdminShell.byId("friendSearch").addEventListener('keydown', event => { if (event.key === 'Enter') searchFriends(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') closeFriendRemark(); });
    AdminShell.registerActions({
      "refresh-friends": refreshFriends,
      "search-friends": searchFriends,
      "clear-friend-search": () => { AdminShell.byId("friendSearch").value = ''; searchFriends(); },
      "friends-prev": () => { friendOffset = Math.max(0, friendOffset - friendLimit); loadFriendList(); },
      "friends-next": () => { friendOffset += friendLimit; loadFriendList(); },
      "requests-prev": () => { requestOffset = Math.max(0, requestOffset - friendLimit); loadFriendRequests(); },
      "requests-next": () => { requestOffset += friendLimit; loadFriendRequests(); },
      "accept-friend-request": element => respondFriendRequest(element, true),
      "reject-friend-request": element => respondFriendRequest(element, false),
      "edit-friend-remark": editFriendRemark,
      "close-friend-remark": closeFriendRemark,
      "save-friend-remark": saveFriendRemark,
    });
    AdminShell.bootstrapAuth({ page: "friends", loggedInText: "已登录好友管理", onLogin: refreshFriends,
      onLoggedOut: () => { closeFriendRemark(); friendGeneration++; requestGeneration++; friendState("等待登录", "warning"); },
      onLoginError: () => friendState("登录失败", "error"),
    });

import re

with open('templates/member_kyc_verify.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Normalise to LF for matching
c = content.replace('\r\n', '\n')

# ─── DROPZONE 1 ───────────────────────────────────────────────
OLD1 = (
    '                <div class="admin-form-group" style="margin-bottom: 25px;">\n'
    '                    <div class="upload-dropzone" id="dropzoneStep1" style="border: 2px dashed var(--border-color); border-radius: 12px; padding: 45px 20px; text-align: center; cursor: pointer; transition: all 0.3s; background: rgba(255, 255, 255, 0.01);">\n'
    '                        <svg width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" style="color: var(--primary); margin-bottom: 15px; transition: transform 0.3s ease;">\n'
    '                            <path stroke-linecap="round" stroke-linejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z"></path>\n'
    '                        </svg>\n'
    '                        <div style="font-size: 1rem; font-weight: 700; color: var(--text-main); margin-bottom: 6px;">Drag &amp; Drop document scans here</div>\n'
    '                        <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 15px;">or click to browse files</div>\n'
    '                        <span style="font-size: 0.75rem; color: var(--text-muted); padding: 6px 14px; background: var(--bg-body); border-radius: 6px; border: 1px solid var(--border-color); display: inline-block;">PDF, PNG, JPG (Max 10MB)</span>\n'
    '                        <input type="file" name="kyc_documents" id="kycDocumentsInput" required multiple accept="image/*,.pdf" style="display: none;">\n'
    '                    </div>\n'
    '                    <div class="selected-files-list" id="filesListStep1" style="margin-top: 15px; display: flex; flex-direction: column; gap: 8px;"></div>\n'
    '                </div>'
)

NEW1 = (
    '                <div class="admin-form-group" style="margin-bottom: 25px;">\n'
    '                    <div class="upload-dropzone" id="dropzoneStep1" style="border: 2px dashed var(--border-color); border-radius: 12px; overflow: hidden; cursor: pointer; transition: all 0.3s; background: rgba(255,255,255,0.01); position: relative; min-height: 160px;">\n'
    '                        <div id="dropzone1Placeholder" style="padding: 45px 20px; text-align: center;">\n'
    '                            <svg width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" style="color: var(--primary); margin-bottom: 15px; transition: transform 0.3s ease;">\n'
    '                                <path stroke-linecap="round" stroke-linejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z"></path>\n'
    '                            </svg>\n'
    '                            <div style="font-size: 1rem; font-weight: 700; color: var(--text-main); margin-bottom: 6px;">Drag &amp; Drop document scans here</div>\n'
    '                            <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 15px;">or click to browse files</div>\n'
    '                            <span style="font-size: 0.75rem; color: var(--text-muted); padding: 6px 14px; background: var(--bg-body); border-radius: 6px; border: 1px solid var(--border-color); display: inline-block;">PDF, PNG, JPG (Max 10MB)</span>\n'
    '                        </div>\n'
    '                        <div id="thumbGrid1" style="display: none; padding: 16px;"></div>\n'
    '                        <input type="file" name="kyc_documents" id="kycDocumentsInput" required multiple accept="image/*,.pdf" style="display: none;">\n'
    '                    </div>\n'
    '                    <div class="selected-files-list" id="filesListStep1" style="margin-top: 10px; display: flex; flex-direction: column; gap: 8px;"></div>\n'
    '                </div>'
)

if OLD1 in c:
    c = c.replace(OLD1, NEW1, 1)
    print('Dropzone 1 patched OK')
else:
    print('ERROR: Dropzone 1 not found')

# ─── DROPZONE 3 ───────────────────────────────────────────────
OLD3 = (
    '                    <div class="upload-dropzone" id="dropzoneStep3" style="border: 2px dashed var(--border-color); border-radius: 12px; padding: 45px 20px; text-align: center; cursor: pointer; transition: all 0.3s; background: rgba(255, 255, 255, 0.01);">\n'
    '                        <svg width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" style="color: var(--primary); margin-bottom: 15px; transition: transform 0.3s ease;">\n'
    '                            <path stroke-linecap="round" stroke-linejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z"></path>\n'
    '                        </svg>\n'
    '                        <div style="font-size: 1rem; font-weight: 700; color: var(--text-main); margin-bottom: 6px;">Drag &amp; Drop proof screenshots here</div>\n'
    '                        <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 15px;">or click to browse files</div>\n'
    '                        <span style="font-size: 0.75rem; color: var(--text-muted); padding: 6px 14px; background: var(--bg-body); border-radius: 6px; border: 1px solid var(--border-color); display: inline-block;">PDF, PNG, JPG (Max 10MB)</span>\n'
    '                        <input type="file" name="post_kyc_documents" id="postKycDocumentsInput" multiple accept="image/*,.pdf" style="display: none;">\n'
    '                    </div>\n'
    '                    <div class="selected-files-list" id="filesListStep3" style="margin-top: 15px; display: flex; flex-direction: column; gap: 8px;"></div>'
)

NEW3 = (
    '                    <div class="upload-dropzone" id="dropzoneStep3" style="border: 2px dashed var(--border-color); border-radius: 12px; overflow: hidden; cursor: pointer; transition: all 0.3s; background: rgba(255,255,255,0.01); position: relative; min-height: 160px;">\n'
    '                        <div id="dropzone3Placeholder" style="padding: 45px 20px; text-align: center;">\n'
    '                            <svg width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" style="color: var(--primary); margin-bottom: 15px; transition: transform 0.3s ease;">\n'
    '                                <path stroke-linecap="round" stroke-linejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z"></path>\n'
    '                            </svg>\n'
    '                            <div style="font-size: 1rem; font-weight: 700; color: var(--text-main); margin-bottom: 6px;">Drag &amp; Drop proof screenshots here</div>\n'
    '                            <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 15px;">or click to browse files</div>\n'
    '                            <span style="font-size: 0.75rem; color: var(--text-muted); padding: 6px 14px; background: var(--bg-body); border-radius: 6px; border: 1px solid var(--border-color); display: inline-block;">PDF, PNG, JPG (Max 10MB)</span>\n'
    '                        </div>\n'
    '                        <div id="thumbGrid3" style="display: none; padding: 16px;"></div>\n'
    '                        <input type="file" name="post_kyc_documents" id="postKycDocumentsInput" multiple accept="image/*,.pdf" style="display: none;">\n'
    '                    </div>\n'
    '                    <div class="selected-files-list" id="filesListStep3" style="margin-top: 10px; display: flex; flex-direction: column; gap: 8px;"></div>'
)

if OLD3 in c:
    c = c.replace(OLD3, NEW3, 1)
    print('Dropzone 3 patched OK')
else:
    print('ERROR: Dropzone 3 not found')

# ─── REPLACE JS updateFilesList functions ──────────────────────
OLD_JS = '''    function updateFilesList() {
        if (!filesList || !fileInput) return;
        filesList.innerHTML = '';
        if (fileInput.files.length > 0) {
            Array.from(fileInput.files).forEach((file) => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 12px 18px; background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border-color); border-radius: 10px; font-size: 0.85rem; width: 100%; box-sizing: border-box; gap: 10px; overflow: hidden;';
                
                const info = document.createElement('div');
                info.style.cssText = 'display: flex; align-items: center; gap: 10px; color: var(--text-main); font-weight: 600; min-width: 0; flex: 1; overflow: hidden;';
                info.innerHTML = `
                    <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="color: var(--primary); flex-shrink: 0;"><path d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
                    <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block;">${file.name}</span>
                `;
                
                const sizeSpan = document.createElement('span');
                sizeSpan.style.cssText = 'font-size: 0.75rem; color: var(--text-muted); font-weight: normal; flex-shrink: 0; white-space: nowrap;';
                sizeSpan.textContent = `(${(file.size / (1024 * 1024)).toFixed(2)} MB)`;
                
                item.appendChild(info);
                item.appendChild(sizeSpan);
                filesList.appendChild(item);
            });
        }
    }'''

NEW_JS = '''    function renderThumbs(files, gridId, placeholderId, inputEl) {
        const grid = document.getElementById(gridId);
        const ph   = document.getElementById(placeholderId);
        if (!grid) return;
        grid.innerHTML = '';
        if (!files || files.length === 0) {
            grid.style.display = 'none';
            if (ph) ph.style.display = 'block';
            return;
        }
        if (ph) ph.style.display = 'none';
        grid.style.display = 'block';

        const wrap = document.createElement('div');
        wrap.style.cssText = 'display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 12px;';

        Array.from(files).forEach(file => {
            const card = document.createElement('div');
            card.style.cssText = 'border-radius: 10px; overflow: hidden; border: 1px solid var(--border-color); background: rgba(255,255,255,0.03); position: relative; aspect-ratio: 1;';

            if (file.type.startsWith('image/')) {
                const img = document.createElement('img');
                img.src = URL.createObjectURL(file);
                img.style.cssText = 'width: 100%; height: 100%; object-fit: cover; display: block;';
                card.appendChild(img);
            } else {
                // PDF or other
                card.style.cssText += 'display:flex; flex-direction:column; align-items:center; justify-content:center; gap:6px; padding:10px; text-align:center;';
                card.innerHTML = '<svg width="36" height="36" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" style="color:var(--primary);"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"></path></svg>' +
                    '<span style="font-size:0.68rem; color:var(--text-muted); word-break:break-all; line-height:1.3;">' + file.name.split('.').pop().toUpperCase() + '</span>';
            }

            // filename tooltip overlay at bottom
            const nameBar = document.createElement('div');
            nameBar.style.cssText = 'position:absolute; bottom:0; left:0; right:0; background:rgba(0,0,0,0.6); color:#fff; font-size:0.62rem; padding:3px 6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; backdrop-filter:blur(4px);';
            nameBar.textContent = file.name;
            card.appendChild(nameBar);

            wrap.appendChild(card);
        });

        // "change files" link
        const changeRow = document.createElement('div');
        changeRow.style.cssText = 'margin-top: 10px; text-align: center;';
        changeRow.innerHTML = '<button type="button" onclick="' + inputEl + '.click()" style="background:transparent;border:1px solid var(--border-color);color:var(--text-muted);font-size:0.8rem;padding:6px 16px;border-radius:50px;cursor:pointer;">Change files</button>';

        grid.appendChild(wrap);
        grid.appendChild(changeRow);
    }

    function updateFilesList() {
        if (!filesList || !fileInput) return;
        filesList.innerHTML = '';
        renderThumbs(fileInput.files, 'thumbGrid1', 'dropzone1Placeholder', 'kycDocumentsInput');
        if (fileInput.files.length > 0) {
            Array.from(fileInput.files).forEach((file) => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 10px; font-size: 0.82rem; width: 100%; box-sizing: border-box; gap: 10px; overflow: hidden;';
                const info = document.createElement('div');
                info.style.cssText = 'display: flex; align-items: center; gap: 8px; color: var(--text-main); font-weight: 600; min-width: 0; flex: 1; overflow: hidden;';
                info.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="color:var(--primary);flex-shrink:0;"><path d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg><span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + file.name + '</span>';
                const sizeSpan = document.createElement('span');
                sizeSpan.style.cssText = 'font-size:0.72rem;color:var(--text-muted);flex-shrink:0;white-space:nowrap;';
                sizeSpan.textContent = '(' + (file.size/(1024*1024)).toFixed(2) + ' MB)';
                item.appendChild(info); item.appendChild(sizeSpan); filesList.appendChild(item);
            });
        }
    }'''

if OLD_JS in c:
    c = c.replace(OLD_JS, NEW_JS, 1)
    print('JS updateFilesList patched OK')
else:
    print('ERROR: updateFilesList JS block not found')

OLD_JS3 = '''    function updateFilesList3() {
        if (!filesList3 || !fileInput3) return;
        filesList3.innerHTML = '';
        if (fileInput3.files.length > 0) {
            Array.from(fileInput3.files).forEach((file) => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 12px 18px; background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border-color); border-radius: 10px; font-size: 0.85rem; width: 100%; box-sizing: border-box; gap: 10px; overflow: hidden;';
                
                const info = document.createElement('div');
                info.style.cssText = 'display: flex; align-items: center; gap: 10px; color: var(--text-main); font-weight: 600; min-width: 0; flex: 1; overflow: hidden;';
                info.innerHTML = `
                    <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="color: var(--primary); flex-shrink: 0;"><path d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
                    <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block;">${file.name}</span>
                `;
                
                const sizeSpan = document.createElement('span');
                sizeSpan.style.cssText = 'font-size: 0.75rem; color: var(--text-muted); font-weight: normal; flex-shrink: 0; white-space: nowrap;';
                sizeSpan.textContent = `(${(file.size / (1024 * 1024)).toFixed(2)} MB)`;
                
                item.appendChild(info);
                item.appendChild(sizeSpan);
                filesList3.appendChild(item);
            });
        }
    }'''

NEW_JS3 = '''    function updateFilesList3() {
        if (!filesList3 || !fileInput3) return;
        filesList3.innerHTML = '';
        renderThumbs(fileInput3.files, 'thumbGrid3', 'dropzone3Placeholder', 'postKycDocumentsInput');
        if (fileInput3.files.length > 0) {
            Array.from(fileInput3.files).forEach((file) => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 10px; font-size: 0.82rem; width: 100%; box-sizing: border-box; gap: 10px; overflow: hidden;';
                const info = document.createElement('div');
                info.style.cssText = 'display: flex; align-items: center; gap: 8px; color: var(--text-main); font-weight: 600; min-width: 0; flex: 1; overflow: hidden;';
                info.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="color:var(--primary);flex-shrink:0;"><path d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg><span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + file.name + '</span>';
                const sizeSpan = document.createElement('span');
                sizeSpan.style.cssText = 'font-size:0.72rem;color:var(--text-muted);flex-shrink:0;white-space:nowrap;';
                sizeSpan.textContent = '(' + (file.size/(1024*1024)).toFixed(2) + ' MB)';
                item.appendChild(info); item.appendChild(sizeSpan); filesList3.appendChild(item);
            });
        }
    }'''

if OLD_JS3 in c:
    c = c.replace(OLD_JS3, NEW_JS3, 1)
    print('JS updateFilesList3 patched OK')
else:
    print('ERROR: updateFilesList3 JS block not found')

with open('templates/member_kyc_verify.html', 'w', encoding='utf-8') as f:
    f.write(c)

print('Done.')

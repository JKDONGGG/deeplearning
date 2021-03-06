import SinGAN.functions as functions
import SinGAN.models as models
import os
import torch.nn as nn
import torch.optim as optim
import torch.utils.data
import math
import matplotlib.pyplot as plt
from SinGAN.imresize import imresize

def train(opt, Gs, Zs, reals, NoiseAmp):
    real_ = functions.read_image(opt)  # 训练图像
    in_s = 0  #
    scale_num = 0  # 层数
    print('opt.scale1 : %d' % opt.scale1)
    real = imresize(real_, opt.scale1, opt)
    print(real.shape)
    reals = functions.creat_reals_pyramid(real, reals, opt)  # 生成图像的金字塔
    print('len(reals) = %d  opt.stop_scale = %d ' % (len(reals), opt.stop_scale))
    nfc_prev = 0

    # 训练每一层
    while scale_num < opt.stop_scale + 1:
        opt.nfc = min(opt.nfc_init * pow(2, math.floor(scale_num / 4)), 128)
        opt.min_nfc = min(opt.min_nfc_init * pow(2, math.floor(scale_num / 4)), 128)
        print('opt.nfc = %d  opt.min_nfc = %d' % (opt.nfc, opt.min_nfc))
        opt.out_ = functions.generate_dir2save(opt)  # 保存路径
        opt.outf = '%s/%d' % (opt.out_, scale_num)
        try:
            os.makedirs(opt.outf)
        except OSError:
                pass

        #plt.imsave('%s/in.png' %  (opt.out_), functions.convert_image_np(real), vmin=0, vmax=1)
        #plt.imsave('%s/original.png' %  (opt.out_), functions.convert_image_np(real_), vmin=0, vmax=1)
        plt.imsave('%s/real_scale.png' %  (opt.outf), functions.convert_image_np(reals[scale_num]), vmin = 0, vmax = 1)  # 保存当前层尺度的真实图像

        D_curr, G_curr = init_models(opt)
        if (nfc_prev == opt.nfc):  # 上下层的卷积核数量相同  就用下层的网络
            G_curr.load_state_dict(torch.load('%s/%d/netG.pth' % (opt.out_, scale_num - 1)))
            D_curr.load_state_dict(torch.load('%s/%d/netD.pth' % (opt.out_, scale_num - 1)))
        # 训练单个尺度
        z_curr, in_s, G_curr = train_single_scale(D_curr, G_curr, reals, Gs, Zs, in_s, NoiseAmp, opt)

        G_curr = functions.reset_grads(G_curr, False)
        G_curr.eval()  #
        D_curr = functions.reset_grads(D_curr, False)
        D_curr.eval()

        Gs.append(G_curr)
        Zs.append(z_curr)
        NoiseAmp.append(opt.noise_amp)

        torch.save(Zs, '%s/Zs.pth' % (opt.out_))
        torch.save(Gs, '%s/Gs.pth' % (opt.out_))
        torch.save(reals, '%s/reals.pth' % (opt.out_))
        torch.save(NoiseAmp, '%s/NoiseAmp.pth' % (opt.out_))

        scale_num += 1
        nfc_prev = opt.nfc
        del D_curr, G_curr
    return



def train_single_scale(netD, netG, reals, Gs, Zs, in_s, NoiseAmp, opt, centers = None):

    real = reals[len(Gs)]  # 取当前层尺度的图像
    opt.nzx = real.shape[2]#+(opt.ker_size-1)*(opt.num_layer)  # 第 n 层输入的噪声 z 的维度 x
    opt.nzy = real.shape[3]#+(opt.ker_size-1)*(opt.num_layer)
    opt.receptive_field = opt.ker_size + ((opt.ker_size - 1) * (opt.num_layer - 1)) * opt.stride
    pad_noise = int(((opt.ker_size - 1) * opt.num_layer) / 2)  # 计算每个边界需要填充的行数
    pad_image = int(((opt.ker_size - 1) * opt.num_layer) / 2)
    if opt.mode == 'animation_train':
        opt.nzx = real.shape[2] + (opt.ker_size - 1) * (opt.num_layer)
        opt.nzy = real.shape[3] + (opt.ker_size - 1) * (opt.num_layer)
        pad_noise = 0
    m_noise = nn.ZeroPad2d(int(pad_noise))  # 每个边填充 pad_noise 个 0
    m_image = nn.ZeroPad2d(int(pad_image))

    alpha = opt.alpha

    fixed_noise = functions.generate_noise([opt.nc_z, opt.nzx, opt.nzy], device = opt.device)  # 生成噪声
    z_opt = torch.full(fixed_noise.shape, 0, device = opt.device)  # 输入的噪声置零
    z_opt = m_noise(z_opt)  # 边界填充

    # setup optimizer
    optimizerD = optim.Adam(netD.parameters(), lr = opt.lr_d, betas = (opt.beta1, 0.999))
    optimizerG = optim.Adam(netG.parameters(), lr = opt.lr_g, betas = (opt.beta1, 0.999))
    schedulerD = torch.optim.lr_scheduler.MultiStepLR(optimizer = optimizerD, milestones = [1600], gamma = opt.gamma)
    schedulerG = torch.optim.lr_scheduler.MultiStepLR(optimizer = optimizerG, milestones = [1600], gamma = opt.gamma)

    errD2plot = []
    errG2plot = []
    D_real2plot = []
    D_fake2plot = []
    z_opt2plot = []

    for epoch in range(opt.niter):  # 2000
        if (Gs == []) & (opt.mode != 'SR_train'):  # 第 n （最底层）层训练
            z_opt = functions.generate_noise([1, opt.nzx, opt.nzy], device = opt.device)  # 生成单层图像尺度的随机噪声 的 tensor
            z_opt = m_noise(z_opt.expand(1, 3, opt.nzx, opt.nzy))  # 扩充到和三通道图像的尺度
            noise_ = functions.generate_noise([1, opt.nzx, opt.nzy], device = opt.device)
            noise_ = m_noise(noise_.expand(1, 3, opt.nzx, opt.nzy))
        else:
            noise_ = functions.generate_noise([opt.nc_z, opt.nzx, opt.nzy], device=opt.device)
            noise_ = m_noise(noise_)

        ############################
        # (1) Update D network: maximize D(x) + D(G(z))
        ###########################
        for j in range(opt.Dsteps):  # 3
            # train with real
            netD.zero_grad()  # 梯度清零

            output = netD(real).to(opt.device)
            print(output.size())
            print(output)
            D_real_map = output.detach()
            errD_real = -output.mean()#-a
            errD_real.backward(retain_graph=True)  # 真图像反传一次 保留本次的计算图
            D_x = -errD_real.item()  # 把字典中每对key和value组成一个元组，并把这些元组放在列表中返回。

            # train with fake
            if (j==0) & (epoch == 0):  # 第一次迭代
                if (Gs == []) & (opt.mode != 'SR_train'):  # 最底层
                    prev = torch.full([1, opt.nc_z, opt.nzx, opt.nzy], 0, device=opt.device)  # make tensor and initialization
                    in_s = prev
                    prev = m_image(prev)  # padding  0
                    z_prev = torch.full([1, opt.nc_z, opt.nzx, opt.nzy], 0, device=opt.device)
                    z_prev = m_noise(z_prev)
                    opt.noise_amp = 1
                elif opt.mode == 'SR_train':
                    z_prev = in_s
                    criterion = nn.MSELoss()
                    RMSE = torch.sqrt(criterion(real, z_prev))
                    opt.noise_amp = opt.noise_amp_init * RMSE
                    z_prev = m_image(z_prev)
                    prev = z_prev
                else:  # 最底层的上面各层
                    prev = draw_concat(Gs, Zs, reals, NoiseAmp, in_s, 'rand', m_noise, m_image, opt)
                    prev = m_image(prev)
                    z_prev = draw_concat(Gs, Zs, reals, NoiseAmp, in_s, 'rec', m_noise, m_image, opt)
                    criterion = nn.MSELoss()
                    RMSE = torch.sqrt(criterion(real, z_prev))
                    opt.noise_amp = opt.noise_amp_init * RMSE
                    z_prev = m_image(z_prev)
            else:
                prev = draw_concat(Gs, Zs, reals, NoiseAmp, in_s, 'rand', m_noise, m_image, opt)  # 对下一层上采样
                prev = m_image(prev)

            if opt.mode == 'paint_train':
                prev = functions.quant2centers(prev, centers)
                plt.imsave('%s/prev.png' % (opt.outf), functions.convert_image_np(prev), vmin=0, vmax=1)

            if (Gs == []) & (opt.mode != 'SR_train'):
                noise = noise_   # 最底层就是随机噪声
            else:
                noise = opt.noise_amp * noise_ + prev  # 非最底层就是比例噪声 + 上采样

            fake = netG(noise.detach(), prev)  #切断反向传播的梯度流  生成假图像
            output = netD(fake.detach())   # 防止反向传播到 G 网络
            errD_fake = output.mean()  # 计算D网络判别假图像的输出
            errD_fake.backward(retain_graph=True)  # 假图像反传一次 保留本次的计算图
            D_G_z = output.mean().item()

            gradient_penalty = functions.calc_gradient_penalty(netD, real, fake, opt.lambda_grad, opt.device)  #计算 D 网络的梯度惩罚
            gradient_penalty.backward()  # 梯度惩罚 反传一次 不保留本次的计算图

            errD = errD_real + errD_fake + gradient_penalty  # 计算 D 的总损失
            optimizerD.step()  # 更新一次

        errD2plot.append(errD.detach())  # 截断

        ############################
        # (2) Update G network: maximize D(G(z))
        ###########################

        for j in range(opt.Gsteps):  # 3次训练
            netG.zero_grad()  # 梯度清零
            output = netD(fake)  # 对假头像进行判别
            D_fake_map = output.detach()
            errG = -output.mean()  # 计算 G 损失
            errG.backward(retain_graph=True)  # 反向传递 并保留本次计算图
            print('alpha = %d ' % (alpha))
            if alpha != 0:
                loss = nn.MSELoss()
                if opt.mode == 'paint_train':
                    z_prev = functions.quant2centers(z_prev, centers)
                    plt.imsave('%s/z_prev.png' % (opt.outf), functions.convert_image_np(z_prev), vmin = 0, vmax = 1)
                Z_opt = opt.noise_amp * z_opt + z_prev  # 计算出本层的噪声
                rec_loss = alpha * loss(netG(Z_opt.detach(), z_prev), real)  # 噪声 + 上采样的图像 输入 G 得到 假图像 与真图像计算损失
                rec_loss.backward(retain_graph=True)  # 损失前传 并保留本次计算图
                rec_loss = rec_loss.detach()  # 损失截断
            else:
                Z_opt = z_opt
                rec_loss = 0

            optimizerG.step()  # 优化更新一次

        errG2plot.append(errG.detach() + rec_loss)  # 保存 G 的损失
        D_real2plot.append(D_x)  # 保存 D 的真实损失
        D_fake2plot.append(D_G_z)  # 保存 D 的假损失
        z_opt2plot.append(rec_loss)  # 保存噪声 z 的损失

        if epoch % 25 == 0 or epoch == (opt.niter - 1):  # 训练25次输出一次
            print('scale %d:[%d/%d]' % (len(Gs), epoch, opt.niter))

        if epoch % 500 == 0 or epoch == (opt.niter - 1):  # 训练 500 次保存一次
            plt.imsave('%s/fake_sample.png' %  (opt.outf), functions.convert_image_np(fake.detach()), vmin = 0, vmax = 1)
            plt.imsave('%s/G(z_opt).png'    % (opt.outf),  functions.convert_image_np(netG(Z_opt.detach(), z_prev).detach()), vmin = 0, vmax = 1)
            plt.imsave('%s/D_fake.png'   % (opt.outf), functions.convert_image_np(D_fake_map))
            plt.imsave('%s/D_real.png'   % (opt.outf), functions.convert_image_np(D_real_map))
            plt.imsave('%s/z_opt.png'    % (opt.outf), functions.convert_image_np(z_opt.detach()), vmin = 0, vmax = 1)
            plt.imsave('%s/prev.png'     % (opt.outf), functions.convert_image_np(prev), vmin = 0, vmax = 1)
            plt.imsave('%s/noise.png'    % (opt.outf), functions.convert_image_np(noise), vmin = 0, vmax = 1)
            plt.imsave('%s/z_prev.png'   % (opt.outf), functions.convert_image_np(z_prev), vmin = 0, vmax = 1)


            torch.save(z_opt, '%s/z_opt.pth' % (opt.outf))

        schedulerD.step()
        schedulerG.step()

    functions.save_networks(netG, netD, z_opt, opt)
    return z_opt, in_s, netG

# 上采样
def draw_concat(Gs, Zs, reals, NoiseAmp, in_s, mode, m_noise, m_image, opt):
    G_z = in_s
    if len(Gs) > 0:  # 不是最底层
        if mode == 'rand':  # 随机
            count = 0
            pad_noise = int(((opt.ker_size-1) * opt.num_layer) / 2)  # 计算噪声填充的边数
            if opt.mode == 'animation_train':
                pad_noise = 0
            for G, Z_opt, real_curr, real_next, noise_amp in zip(Gs, Zs, reals, reals[1:], NoiseAmp):
                if count == 0:
                    z = functions.generate_noise([1, Z_opt.shape[2] - 2 * pad_noise, Z_opt.shape[3] - 2 * pad_noise], device = opt.device)
                    z = z.expand(1, 3, z.shape[2], z.shape[3])
                else:
                    z = functions.generate_noise([opt.nc_z, Z_opt.shape[2] - 2 * pad_noise, Z_opt.shape[3] - 2 * pad_noise], device = opt.device)
                z = m_noise(z)
                G_z = G_z[:, :, 0:real_curr.shape[2], 0:real_curr.shape[3]]
                G_z = m_image(G_z)
                z_in = noise_amp * z + G_z
                G_z = G(z_in.detach(), G_z)  # 生成假照片
                G_z = imresize(G_z, 1 / opt.scale_factor, opt)
                G_z = G_z[:, :, 0:real_next.shape[2], 0:real_next.shape[3]]
                count += 1
        if mode == 'rec':  # 与之前有关
            count = 0
            for G, Z_opt, real_curr, real_next, noise_amp in zip(Gs, Zs, reals, reals[1:], NoiseAmp):
                G_z = G_z[:, :, 0:real_curr.shape[2], 0:real_curr.shape[3]]
                G_z = m_image(G_z)
                z_in = noise_amp * Z_opt + G_z
                G_z = G(z_in.detach(), G_z)
                G_z = imresize(G_z, 1 / opt.scale_factor, opt)
                G_z = G_z[:, :, 0:real_next.shape[2], 0:real_next.shape[3]]
                #if count != (len(Gs)-1):
                #    G_z = m_image(G_z)
                count += 1
    return G_z

def train_paint(opt, Gs, Zs, reals, NoiseAmp, centers, paint_inject_scale):
    in_s = torch.full(reals[0].shape, 0, device = opt.device)
    scale_num = 0
    nfc_prev = 0

    while scale_num < opt.stop_scale + 1:
        if scale_num != paint_inject_scale:
            scale_num += 1
            nfc_prev = opt.nfc
            continue
        else:
            opt.nfc = min(opt.nfc_init * pow(2, math.floor(scale_num / 4)), 128)
            opt.min_nfc = min(opt.min_nfc_init * pow(2, math.floor(scale_num / 4)), 128)

            opt.out_ = functions.generate_dir2save(opt)
            opt.outf = '%s/%d' % (opt.out_, scale_num)
            try:
                os.makedirs(opt.outf)
            except OSError:
                    pass

            #plt.imsave('%s/in.png' %  (opt.out_), functions.convert_image_np(real), vmin=0, vmax=1)
            #plt.imsave('%s/original.png' %  (opt.out_), functions.convert_image_np(real_), vmin=0, vmax=1)
            plt.imsave('%s/in_scale.png' %  (opt.outf), functions.convert_image_np(reals[scale_num]), vmin=0, vmax=1)

            D_curr, G_curr = init_models(opt)

            z_curr, in_s, G_curr = train_single_scale(D_curr, G_curr, reals[:scale_num+1], Gs[:scale_num], Zs[:scale_num], in_s, NoiseAmp[:scale_num], opt, centers=centers)

            G_curr = functions.reset_grads(G_curr, False)
            G_curr.eval()
            D_curr = functions.reset_grads(D_curr, False)
            D_curr.eval()

            Gs[scale_num] = G_curr
            Zs[scale_num] = z_curr
            NoiseAmp[scale_num] = opt.noise_amp

            torch.save(Zs, '%s/Zs.pth' % (opt.out_))
            torch.save(Gs, '%s/Gs.pth' % (opt.out_))
            torch.save(reals, '%s/reals.pth' % (opt.out_))
            torch.save(NoiseAmp, '%s/NoiseAmp.pth' % (opt.out_))

            scale_num+=1
            nfc_prev = opt.nfc
        del D_curr, G_curr
    return


def init_models(opt):

    #generator initialization:
    netG = models.GeneratorConcatSkip2CleanAdd(opt).to(opt.device)
    netG.apply(models.weights_init)
    if opt.netG != '':
        netG.load_state_dict(torch.load(opt.netG))
    print(netG)

    #discriminator initialization:
    netD = models.WDiscriminator(opt).to(opt.device)
    netD.apply(models.weights_init)
    if opt.netD != '':
        netD.load_state_dict(torch.load(opt.netD))
    print(netD)

    return netD, netG
